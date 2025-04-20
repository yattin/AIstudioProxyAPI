// server.cjs (优化版 v2.17 - 增加日志ID & 常量)

const express = require('express');
const fs = require('fs');
const path = require('path');
const cors = require('cors');

// --- 依赖检查 ---
let playwright;
let expect;
try {
    playwright = require('playwright');
    expect = require('@playwright/test').expect;
} catch (e) {
    console.error("❌ 错误: 依赖模块未找到。请运行:");
    console.error("   npm install express playwright @playwright/test cors");
    process.exit(1);
}

// --- 配置 ---
const SERVER_PORT = process.env.PORT || 3000;
const CHROME_DEBUGGING_PORT = 8848;
const CDP_ADDRESS = `http://127.0.0.1:${CHROME_DEBUGGING_PORT}`;
const AI_STUDIO_URL_PATTERN = 'aistudio.google.com/';
const RESPONSE_COMPLETION_TIMEOUT = 300000; // 5分钟总超时
const POLLING_INTERVAL = 250; // 流式检查间隔
// v2.12: Timeout for secondary checks *after* spinner disappears
const POST_SPINNER_CHECK_DELAY_MS = 500; // Spinner消失后稍作等待再检查其他状态
const FINAL_STATE_CHECK_TIMEOUT_MS = 1500; // 检查按钮和输入框最终状态的超时
const SPINNER_CHECK_TIMEOUT_MS = 1000; // 检查Spinner状态的超时
const POST_COMPLETION_BUFFER = 1500; // JSON模式下可以缩短检查后等待时间

// --- 常量 ---
const MODEL_NAME = 'google-ai-studio-via-playwright-cdp-json';
const CHAT_COMPLETION_ID_PREFIX = 'chatcmpl-';

// --- 选择器常量 ---
const INPUT_SELECTOR = 'ms-prompt-input-wrapper textarea';
const SUBMIT_BUTTON_SELECTOR = 'button[aria-label="Run"]';
const RESPONSE_CONTAINER_SELECTOR = 'ms-chat-turn .chat-turn-container.model';
const RESPONSE_TEXT_SELECTOR = 'ms-cmark-node.cmark-node'; // Target the container for raw text
const LOADING_SPINNER_SELECTOR = 'button[aria-label="Run"] svg .stoppable-spinner'; // Spinner circle
const ERROR_TOAST_SELECTOR = 'div.toast.warning, div.toast.error'; // 页面错误提示

// v2.16: JSON Structure Prompt (Renamed)
const prepareAIStudioPrompt = (userPrompt, systemPrompt = null) => {
    let fullPrompt = `
IMPORTANT: Your entire response MUST be a single JSON object. Do not include any text outside of this JSON object.
The JSON object must have a single key named "response". The value of the "response" key must be your complete answer to the user's prompt.
`;

    if (systemPrompt && systemPrompt.trim() !== '') {
        fullPrompt += `\nSystem Instruction: ${systemPrompt}\n`;
    }

    fullPrompt += `
Example:
User asks: "What is the capital of France?"
Your response MUST be:
{
  "response": "The capital of France is Paris."
}

User asks: "Write a python function to add two numbers"
Your response MUST be:
{
  "response": "\\\`\\\`\\\`python\\ndef add(a, b):\\n  return a + b\\n\\\`\\\`\\\`"
}

Now, answer the following user prompt, ensuring your output strictly adheres to the JSON format described above:

User Prompt: "${userPrompt}"

Your JSON Response:
`;
    return fullPrompt;
};

const app = express();

// --- 全局变量 ---
let browser = null;
let page = null;
let isPlaywrightReady = false;
let isInitializing = false;

// --- Playwright 初始化函数 ---
async function initializePlaywright() {
    if (isPlaywrightReady || isInitializing) return;
    isInitializing = true;
    console.log(`--- 初始化 Playwright: 连接到 ${CDP_ADDRESS} ---`);

    try {
        browser = await playwright.chromium.connectOverCDP(CDP_ADDRESS, { timeout: 20000 });
        console.log('✅ 成功连接到正在运行的 Chrome 实例！');

        browser.once('disconnected', () => {
            console.error('❌ Playwright 与 Chrome 的连接已断开！');
            isPlaywrightReady = false;
            browser = null;
            page = null;
        });

        await new Promise(resolve => setTimeout(resolve, 500));

        const contexts = browser.contexts();
        let context;
        if (!contexts || contexts.length === 0) {
             await new Promise(resolve => setTimeout(resolve, 1500));
             const retryContexts = browser.contexts();
             if (!retryContexts || retryContexts.length === 0) {
                 throw new Error('无法获取浏览器上下文。请检查 Chrome 是否已正确启动并响应。');
             }
             context = retryContexts[0];
        } else {
             context = contexts[0];
        }

        let foundPage = null;
        const pages = context.pages();
        console.log(`-> 发现 ${pages.length} 个页面。正在搜索 AI Studio (匹配 "${AI_STUDIO_URL_PATTERN}")...`);
        for (const p of pages) {
            try {
                 if (p.isClosed()) continue;
                const url = p.url();
                if (url.includes(AI_STUDIO_URL_PATTERN) && url.includes('/prompts/')) {
                    console.log(`-> 找到 AI Studio 页面: ${url}`);
                    foundPage = p;
                    break;
                }
            } catch (pageError) {
                 if (!p.isClosed()) {
                     console.warn(`   警告：评估页面 URL 时出错: ${pageError.message.split('\\n')[0]}`);
                 }
            }
        }

        if (!foundPage) {
            throw new Error(`未在已连接的 Chrome 中找到包含 "${AI_STUDIO_URL_PATTERN}" 和 "/prompts/" 的页面。请确保 auto_connect_aistudio.js 已成功运行，并且 AI Studio 页面 (例如 prompts/new_chat) 已打开。`);
        }

        page = foundPage;
        console.log('-> 已定位到 AI Studio 页面。');
        await page.bringToFront();
        console.log('-> 尝试将页面置于前台。检查加载状态...');
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 });
        console.log('-> 页面 DOM 已加载。');

        try {
            console.log("-> 尝试定位核心输入区域以确认页面就绪...");
            await page.locator('ms-prompt-input-wrapper').waitFor({ state: 'visible', timeout: 15000 });
             console.log("-> 核心输入区域容器已找到。");
        } catch(initCheckError) {
            console.warn(`⚠️ 初始化检查警告：未能快速定位到核心输入区域容器。页面可能仍在加载或结构有变: ${initCheckError.message.split('\\n')[0]}`);
            await saveErrorSnapshot('init_check_fail');
        }

        isPlaywrightReady = true;
        console.log('✅ Playwright 已准备就绪。');

    } catch (error) {
        console.error(`❌ 初始化 Playwright 失败: ${error.message}`);
        await saveErrorSnapshot('init_fail');
        isPlaywrightReady = false;
        browser = null;
        page = null;
    } finally {
        isInitializing = false;
    }
}

// --- 中间件 ---
app.use(cors());
app.use(express.json());

// --- Web UI Route ---
app.get('/', (req, res) => {
    const htmlPath = path.join(__dirname, 'index.html');
    if (fs.existsSync(htmlPath)) {
        res.sendFile(htmlPath);
    } else {
        res.status(404).send('Error: index.html not found.');
    }
});

// --- 健康检查 ---
app.get('/health', (req, res) => {
    const isConnected = browser?.isConnected() ?? false;
    const isPageValid = page && !page.isClosed();
    if (isPlaywrightReady && isPageValid && isConnected) {
        res.status(200).json({ status: 'OK', message: 'Server running, Playwright connected, page valid.' });
    } else {
        const reasons = [];
        if (!isPlaywrightReady) reasons.push("Playwright not initialized or ready");
        if (!isPageValid) reasons.push("Target page not found or closed");
        if (!isConnected) reasons.push("Browser disconnected");
        if (isInitializing) reasons.push("Playwright is currently initializing");
        res.status(503).json({ status: 'Error', message: `Service Unavailable. Issues: ${reasons.join(', ')}.` });
    }
});

// --- 新增：API 辅助函数 ---

// 验证聊天请求
function validateChatRequest(messages) {
    if (!messages || !Array.isArray(messages) || messages.length === 0) {
        throw new Error('Invalid request: "messages" array is missing or empty.');
    }
    const lastUserMessage = messages.filter(msg => msg.role === 'user').pop();
    if (!lastUserMessage || !lastUserMessage.content) {
        throw new Error('Invalid request: No valid user message content found in the "messages" array.');
    }
    return {
        userPrompt: lastUserMessage.content,
        systemPrompt: messages.find(msg => msg.role === 'system')?.content
    };
}

// 与页面交互并提交 Prompt
async function interactAndSubmitPrompt(page, prompt, reqId) {
    console.log(`[${reqId}] 开始页面交互...`);
    const inputField = page.locator(INPUT_SELECTOR);
    const submitButton = page.locator(SUBMIT_BUTTON_SELECTOR);
    const loadingSpinner = page.locator(LOADING_SPINNER_SELECTOR); // Keep spinner locator here for later use

    console.log(`[${reqId}]  - 等待输入框可用...`);
    try {
        await inputField.waitFor({ state: 'visible', timeout: 10000 });
    } catch (e) {
         console.error(`[${reqId}] ❌ 查找输入框失败！`);
         await saveErrorSnapshot(`input_field_not_visible_${reqId}`);
         throw new Error(`[${reqId}] Failed to find visible input field. Error: ${e.message}`);
    }

    console.log(`[${reqId}]  - 清空并填充输入框...`);
    await inputField.fill(prompt, { timeout: 15000 });

    console.log(`[${reqId}]  - 等待运行按钮可用...`);
    try {
        await expect(submitButton).toBeEnabled({ timeout: 15000 });
    } catch (e) {
        console.error(`[${reqId}] ❌ 等待运行按钮变为可用状态超时！`);
        await saveErrorSnapshot(`submit_button_not_enabled_before_click_${reqId}`);
        throw new Error(`[${reqId}] Submit button not enabled before click. Error: ${e.message}`);
    }

    console.log(`[${reqId}]  - 点击运行按钮...`);
    await submitButton.click({ timeout: 10000 });

    return { inputField, submitButton, loadingSpinner }; // Return locators
}

// 定位最新的回复元素
async function locateResponseElements(page, { inputField, submitButton, loadingSpinner }, reqId) {
    console.log(`[${reqId}] 定位 AI 回复元素...`);
    let lastResponseContainer;
    let responseElement;
    let locatedResponseElements = false;

    for (let i = 0; i < 3 && !locatedResponseElements; i++) {
         try {
             console.log(`[${reqId}]    尝试定位最新回复容器及文本元素 (第 ${i + 1} 次)`);
             await page.waitForTimeout(500 + i * 500); // 固有延迟

             const isEndState = await checkEndConditionQuickly(page, loadingSpinner, inputField, submitButton, 250, reqId);
             const locateTimeout = isEndState ? 3000 : 60000;
             if (isEndState) {
                console.log(`[${reqId}]     -> 检测到结束条件已满足，使用 ${locateTimeout / 1000}s 超时进行定位。`);
             }

             lastResponseContainer = page.locator(RESPONSE_CONTAINER_SELECTOR).last();
             await lastResponseContainer.waitFor({ state: 'attached', timeout: locateTimeout });

             responseElement = lastResponseContainer.locator(RESPONSE_TEXT_SELECTOR);
             await responseElement.waitFor({ state: 'attached', timeout: locateTimeout });

             console.log(`[${reqId}]    回复容器和文本元素定位成功。`);
             locatedResponseElements = true;
         } catch (locateError) {
             console.warn(`[${reqId}]    第 ${i + 1} 次定位回复元素失败: ${locateError.message.split('\n')[0]}`);
             if (i === 2) {
                  await saveErrorSnapshot(`response_locate_fail_${reqId}`);
                  throw new Error(`[${reqId}] Failed to locate response elements after multiple attempts.`);
             }
         }
    }
    if (!locatedResponseElements) throw new Error(`[${reqId}] Could not locate response elements.`);
    return { responseElement, lastResponseContainer }; // Return located elements
}

// --- 新增：处理流式响应 ---
async function handleStreamingResponse(res, responseElement, page, { inputField, submitButton, loadingSpinner }, operationTimer, reqId) {
    console.log(`[${reqId}]   - 流式传输开始 (主要阶段: 轮询直到 Spinner 消失)...`);
    let lastRawText = "";
    let lastSentResponseContent = ""; // Tracks the *extracted* content sent
    let responseKeyDetected = false; // Tracks if outer 'response' key found
    const startTime = Date.now();

    let primaryLoopEnded = false;
    while (Date.now() - startTime < RESPONSE_COMPLETION_TIMEOUT && !primaryLoopEnded) {
        // 1. Get text & parse (including nesting) & send delta
        const currentRawText = await getRawTextContent(responseElement, lastRawText, reqId);
        if (currentRawText !== lastRawText) {
            lastRawText = currentRawText;
            try {
                const parsedJson = tryParseJson(currentRawText, reqId); // 解析最外层
                if (parsedJson && typeof parsedJson.response === 'string') {
                    let potentialResponseString = parsedJson.response;
                    let currentActualContent = potentialResponseString; // 默认使用外层的值

                    // ---- 尝试解析内层 JSON ----
                    try {
                        const innerParsedJson = tryParseJson(potentialResponseString, reqId);
                        if (innerParsedJson && typeof innerParsedJson.response === 'string') {
                             // 如果内层解析成功且有 response，则使用内层的值
                             currentActualContent = innerParsedJson.response;
                         }
                    } catch (innerParseError) { /* Ignore inner parse error */ }
                    // ---- 结束内层处理 ----

                    // First time detecting the response key (or nested response)
                    if (!responseKeyDetected) {
                        console.log(`[${reqId}]    (流式) 检测到 \'response\' 键或嵌套内容，开始传输...`);
                        responseKeyDetected = true;
                    }

                    // Send delta if new content is appended and key was detected
                    // 使用 currentActualContent 进行比较和发送
                    if (responseKeyDetected && currentActualContent.length > lastSentResponseContent.length && currentActualContent.startsWith(lastSentResponseContent)) {
                        const delta = currentActualContent.substring(lastSentResponseContent.length);
                        sendStreamChunk(res, delta, reqId);
                        lastSentResponseContent = currentActualContent; // Update the last sent *extracted* content
                    }
                }
            } catch (parseError) { /* Ignore outer parse errors */ }
        }

        // 2. Check spinner state
        let isSpinnerHidden = false;
        try {
            await expect(loadingSpinner).toBeHidden({ timeout: SPINNER_CHECK_TIMEOUT_MS });
            isSpinnerHidden = true;
        } catch (e) { /* Spinner still visible */ }

        if (isSpinnerHidden) {
            console.log(`[${reqId}]    Spinner 已消失，结束主要轮询阶段。`);
            primaryLoopEnded = true;
        } else {
            // 3. Wait for next poll interval if spinner still visible
            await page.waitForTimeout(2000); // 2-second interval
        }

    } // End primary while loop

     if (!primaryLoopEnded && Date.now() - startTime >= RESPONSE_COMPLETION_TIMEOUT) {
         console.warn(`[${reqId}]   - 主要轮询阶段因总超时结束。`);
         await saveErrorSnapshot(`streaming_primary_timeout_${reqId}`);
         if (!res.writableEnded) {
             sendStreamError(res, "Stream processing timed out during primary phase.", reqId);
             res.end(); // Ensure stream ends
         }
         clearTimeout(operationTimer); // Clear the overall timer as operation failed here
         throw new Error(`[${reqId}] Streaming primary loop timed out.`); // Throw to be caught by main handler
     }

    // --- Post-Spinner Phase ---
    console.log(`[${reqId}]    检查最终页面状态 (输入框空 + 按钮禁用)...`);
    try {
        await expect(inputField).toHaveValue('', { timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
        await expect(submitButton).toBeDisabled({ timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
        console.log(`[${reqId}]    最终页面状态确认成功。`);
    } catch (finalStateError) {
        console.warn(`[${reqId}]    警告: 检查最终页面状态失败或超时: ${finalStateError.message.split('\n')[0]}`);
        // Continue even if final state check fails, as the stream might still finish
    }

    console.log(`[${reqId}]    开始最终 5 秒更新窗口...`);
    const finalWindowStartTime = Date.now();
    while (Date.now() - finalWindowStartTime < 5000) {
         // Get text & parse & send delta (same logic as in primary loop)
        const currentRawText = await getRawTextContent(responseElement, lastRawText, reqId);
         if (currentRawText !== lastRawText) {
            lastRawText = currentRawText;
             try {
                const parsedJson = tryParseJson(currentRawText, reqId); // 解析最外层
                if (parsedJson && typeof parsedJson.response === 'string') {
                    let potentialResponseString = parsedJson.response;
                    let currentActualContent = potentialResponseString;
                    try { // Handle nesting
                        const innerParsedJson = tryParseJson(potentialResponseString, reqId);
                        if (innerParsedJson && typeof innerParsedJson.response === 'string') {
                             currentActualContent = innerParsedJson.response;
                         }
                    } catch (innerParseError) { /* Ignore */ }

                    // No need to check responseKeyDetected again here
                    if (currentActualContent.length > lastSentResponseContent.length && currentActualContent.startsWith(lastSentResponseContent)) {
                        const delta = currentActualContent.substring(lastSentResponseContent.length);
                        sendStreamChunk(res, delta, reqId);
                        lastSentResponseContent = currentActualContent;
                    }
                }
             } catch (parseError) { /* Ignore */ }
         }
         await page.waitForTimeout(500); // Faster polling during final window
    }
    console.log(`[${reqId}]    最终 5 秒更新窗口结束。`);

    // --- End Stream ---
    if (!res.writableEnded) {
        res.write('data: [DONE]\n\n');
        res.end();
        console.log(`[${reqId}] ✅ 流式响应 [DONE] 已发送。`);
        console.log(`[${reqId}]    最终提取的响应内容长度: ${lastSentResponseContent.length}`); // Log extracted length
    }
}

// --- 新增：处理非流式响应 ---
async function handleNonStreamingResponse(res, page, locators, operationTimer, reqId) {
    console.log(`[${reqId}]   - 等待 AI 处理完成 (检查 Spinner 消失 + 输入框空 + 按钮禁用)...`);
    let processComplete = false;
    const nonStreamStartTime = Date.now();
    let finalStateCheckInitiated = false;
    const { inputField, submitButton, loadingSpinner } = locators;

    // Completion check logic
    while (!processComplete && Date.now() - nonStreamStartTime < RESPONSE_COMPLETION_TIMEOUT) {
        let isSpinnerHidden = false;
        let isInputEmpty = false;
        let isButtonDisabled = false;

        try {
            await expect(loadingSpinner).toBeHidden({ timeout: SPINNER_CHECK_TIMEOUT_MS });
            isSpinnerHidden = true;
        } catch { /* Spinner still visible */ }

        if (isSpinnerHidden) {
            try {
                await expect(inputField).toHaveValue('', { timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                isInputEmpty = true;
            } catch { /* Input not empty */ }

            if (isInputEmpty) {
                try {
                    await expect(submitButton).toBeDisabled({ timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                    isButtonDisabled = true;
                } catch { /* Button not disabled */ }
            }
        }

        if (isSpinnerHidden && isInputEmpty && isButtonDisabled) {
            if (!finalStateCheckInitiated) {
                finalStateCheckInitiated = true;
                console.log(`[${reqId}]    检测到潜在最终状态。等待 3 秒进行确认...`);
                await page.waitForTimeout(3000);
                console.log(`[${reqId}]    3 秒等待结束，重新检查状态...`);
                try {
                    await expect(loadingSpinner).toBeHidden({ timeout: 500 });
                    await expect(inputField).toHaveValue('', { timeout: 500 });
                    await expect(submitButton).toBeDisabled({ timeout: 500 });
                    console.log(`[${reqId}]    状态确认成功。判定处理完成。`);
                    processComplete = true;
                } catch (recheckError) {
                    console.log(`[${reqId}]    状态在 3 秒确认期间发生变化 (${recheckError.message.split('\n')[0]})。继续轮询...`);
                    finalStateCheckInitiated = false;
                }
            }
        } else {
            if (finalStateCheckInitiated) {
                console.log(`[${reqId}]    最终状态不再满足，重置确认标志。`);
                finalStateCheckInitiated = false;
            }
             await page.waitForTimeout(POLLING_INTERVAL * 2);
        }
    } // End while loop for completion check

    // Check for Page Errors BEFORE attempting to parse JSON
    console.log(`[${reqId}]   - 检查页面上是否存在错误提示...`);
    const pageError = await detectAndExtractPageError(page, reqId);
    if (pageError) {
        console.error(`[${reqId}] ❌ 检测到 AI Studio 页面错误: ${pageError}`);
        await saveErrorSnapshot(`page_error_detected_${reqId}`);
        throw new Error(`[${reqId}] AI Studio Error: ${pageError}`);
    }

    if (!processComplete) {
         console.warn(`[${reqId}]    警告：等待最终完成状态超时或未能稳定确认 (${(Date.now() - nonStreamStartTime) / 1000}s)。将直接尝试获取并解析JSON。`);
          await saveErrorSnapshot(`nonstream_final_state_timeout_${reqId}`);
     } else {
         console.log(`[${reqId}]   - 开始获取并解析最终 JSON...`);
     }

    // Get and Parse JSON
    let aiResponseText = null;
    const maxRetries = 3;
    let attempts = 0;

    while (attempts < maxRetries && aiResponseText === null) {
         attempts++;
         console.log(`[${reqId}]     - 尝试获取原始文本并解析 JSON (第 ${attempts} 次)...`);
         try {
             // Re-locate response element within the retry loop for robustness
             const { responseElement: currentResponseElement } = await locateResponseElements(page, locators, reqId);

             const rawText = await getRawTextContent(currentResponseElement, '', reqId);

             if (!rawText || rawText.trim() === '') {
                 console.warn(`[${reqId}]     - 第 ${attempts} 次获取的原始文本为空。`);
                 throw new Error("Raw text content is empty.");
             }
              console.log(`[${reqId}]     - 获取到原始文本 (长度: ${rawText.length}): \"${rawText.substring(0,100)}...\"`);

             const parsedJson = tryParseJson(rawText, reqId);

             if (parsedJson && typeof parsedJson.response === 'string') {
                 aiResponseText = parsedJson.response;
                 console.log(`[${reqId}]     - 成功解析 JSON 并提取 \'response\' 字段。`);
                 break;
             } else {
                 console.warn(`[${reqId}]     - 第 ${attempts} 次未能解析 JSON 或缺少 \'response\' 字段。`);
                 if(parsedJson) console.warn(`[${reqId}]       Parsed structure: ${JSON.stringify(parsedJson).substring(0,100)}...`);
                 aiResponseText = null;
                  if (attempts >= maxRetries) {
                     await saveErrorSnapshot(`json_parse_fail_final_attempt_${reqId}`);
                  }
             }

         } catch (e) {
             console.warn(`[${reqId}]     - 第 ${attempts} 次获取或解析失败: ${e.message.split('\n')[0]}`);
             aiResponseText = null;
             if (attempts >= maxRetries) {
                 console.error(`[${reqId}]     - 多次尝试获取并解析 JSON 失败。`);
                 await saveErrorSnapshot(`get_parse_json_failed_final_${reqId}`);
                 aiResponseText = ""; // Fallback to empty string
             } else {
                  await new Promise(resolve => setTimeout(resolve, 1500 + attempts * 500));
             }
         }
    } // End while loop for JSON parsing

    if (aiResponseText === null) {
         console.log(`[${reqId}]     - JSON 解析失败，再次检查页面错误...`);
         const finalCheckError = await detectAndExtractPageError(page, reqId);
         if (finalCheckError) {
              console.error(`[${reqId}] ❌ 检测到 AI Studio 页面错误 (在 JSON 解析失败后): ${finalCheckError}`);
              await saveErrorSnapshot(`page_error_post_json_fail_${reqId}`);
              throw new Error(`[${reqId}] AI Studio Error after JSON parse failed: ${finalCheckError}`);
         }
          console.warn(`[${reqId}] 警告：所有尝试均未能获取并解析出有效的 JSON 回复。返回空回复。`);
          aiResponseText = "";
    }

    // Handle potential nested JSON
    let cleanedResponse = aiResponseText;
    try {
        const innerParsed = tryParseJson(aiResponseText, reqId);
        if (innerParsed && typeof innerParsed.response === 'string') {
            console.log(`[${reqId}]    (非流式) 检测到嵌套 JSON，使用内层 response 内容。`);
            cleanedResponse = innerParsed.response;
        }
    } catch { /* Ignore inner parse error */ }

    console.log(`[${reqId}] ✅ 获取到解析后的 AI 回复 (来自JSON, 长度: ${cleanedResponse.length}): \"${cleanedResponse.substring(0, 100)}...\"`);

    const responsePayload = {
        id: `${CHAT_COMPLETION_ID_PREFIX}${Date.now()}-${Math.random().toString(36).substring(2, 15)}`,
        object: 'chat.completion',
        created: Math.floor(Date.now() / 1000),
        model: MODEL_NAME,
        choices: [{
            index: 0,
            message: { role: 'assistant', content: cleanedResponse },
            finish_reason: 'stop',
        }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    };
    console.log(`[${reqId}] ✅ 返回 JSON 响应。`);
    res.json(responsePayload);
}

// --- API 端点 (重构后) ---
app.post('/v1/chat/completions', async (req, res) => {
    const reqId = Math.random().toString(36).substring(2, 9); // 生成简短的请求 ID
    console.log(`\n[${reqId}] --- 收到 /v1/chat/completions 请求 ---`);

    // 1. 检查 Playwright 状态
    if (!isPlaywrightReady && !isInitializing) {
        console.warn(`[${reqId}] Playwright 未就绪，尝试重新初始化...`);
        await initializePlaywright(); // 注意：initializePlaywright 内部日志无 reqId
    }
    if (!isPlaywrightReady || !page || page.isClosed() || !browser?.isConnected()) {
        console.error(`[${reqId}] API 请求失败：Playwright 未就绪、页面关闭或连接断开。`);
         let detail = 'Unknown issue.';
         if (!browser?.isConnected()) detail = "Browser connection lost.";
         else if (!page || page.isClosed()) detail = "Target AI Studio page is not available or closed.";
         else if (!isPlaywrightReady) detail = "Playwright initialization failed or incomplete.";
         console.error(`[${reqId}] Playwright 连接不可用详情: ${detail}`);
        return res.status(503).json({
            error: { message: `[${reqId}] Playwright connection is not active. ${detail} Please ensure Chrome is running correctly, the AI Studio tab is open, and potentially restart the server.`, type: 'server_error' }
        });
    }

    const { messages, stream, ...otherParams } = req.body;
    const isStreaming = stream === true;
    let operationTimer;

    try {
        console.log(`[${reqId}] 请求模式: ${isStreaming ? '流式 (SSE)' : '非流式 (JSON)'}`);

        // 2. 设置总操作超时
        operationTimer = setTimeout(async () => {
            await saveErrorSnapshot(`operation_timeout_${reqId}`);
            console.error(`[${reqId}] Operation timed out after ${RESPONSE_COMPLETION_TIMEOUT / 1000} seconds.`);
            if (!res.headersSent) {
                 res.status(504).json({ error: { message: `[${reqId}] Operation timed out`, type: 'timeout_error' } });
            } else if (isStreaming && !res.writableEnded) {
                 sendStreamError(res, "Operation timed out on server.", reqId);
            }
        }, RESPONSE_COMPLETION_TIMEOUT);

        // 3. 验证请求
        const { userPrompt, systemPrompt: extractedSystemPrompt } = validateChatRequest(messages);
        const systemPrompt = extractedSystemPrompt || otherParams?.system_prompt; // Combine sources

        console.log(`[${reqId}]   原始 User Prompt (start): \"${userPrompt?.substring(0, 80)}...\"`);
        if (systemPrompt) {
            console.log(`[${reqId}]   System Prompt (start): \"${systemPrompt.substring(0, 80)}...\"`);
        }
        if (Object.keys(otherParams).length > 0) {
             console.log(`[${reqId}]   记录到的额外参数: ${JSON.stringify(otherParams)}`);
        }

        // 4. 准备 Prompt
        const prompt = prepareAIStudioPrompt(userPrompt, systemPrompt);
        console.log(`[${reqId}] 构建的 Prompt (含系统提示): \"${prompt.substring(0, 200)}...\"`);

        // 5. 与页面交互并提交
        const locators = await interactAndSubmitPrompt(page, prompt, reqId);

        // 6. 定位响应元素
        const { responseElement } = await locateResponseElements(page, locators, reqId);

        // 7. 处理响应 (流式或非流式)
        console.log(`[${reqId}] 处理 AI 回复...`);
        if (isStreaming) {
            // --- 设置流式响应头 ---
            res.setHeader('Content-Type', 'text/event-stream');
            res.setHeader('Cache-Control', 'no-cache');
            res.setHeader('Connection', 'keep-alive');
            res.flushHeaders();

            // 调用流式处理函数
            await handleStreamingResponse(res, responseElement, page, locators, operationTimer, reqId);

        } else {
            // 调用非流式处理函数
            await handleNonStreamingResponse(res, page, locators, operationTimer, reqId);
        }

        console.log(`[${reqId}] ✅ 请求处理成功完成。`);
        clearTimeout(operationTimer); // 清除总超时定时器（成功完成）

    } catch (error) {
        clearTimeout(operationTimer); // 确保在任何错误情况下都清除定时器
        console.error(`[${reqId}] ❌ 处理 API 请求时出错: ${error.message}\n${error.stack}`);
        if (!error.message?.includes('snapshot') && !error.stack?.includes('saveErrorSnapshot')) {
             // 避免在保存快照失败时再次尝试保存快照
             await saveErrorSnapshot(`general_api_error_${reqId}`);
        }

        // 发送错误响应
        if (!res.headersSent) {
             // 根据错误类型判断状态码，提供一些常见情况的处理
             let statusCode = 500;
             let errorType = 'server_error';
             if (error.message?.includes('timed out') || error.message?.includes('timeout')) {
                 statusCode = 504; // Gateway Timeout
                 errorType = 'timeout_error';
             } else if (error.message?.includes('AI Studio Error')) {
                 statusCode = 502; // Bad Gateway (error from upstream)
                 errorType = 'upstream_error';
             } else if (error.message?.includes('Invalid request')) {
                 statusCode = 400; // Bad Request
                 errorType = 'invalid_request_error';
             }
            res.status(statusCode).json({ error: { message: `[${reqId}] ${error.message}`, type: errorType } });
        } else if (isStreaming && !res.writableEnded) {
             // 如果是流式响应且头部已发送，则发送流式错误
             sendStreamError(res, error.message, reqId);
        }
        else if (!res.writableEnded) {
             // 对于非流式但已发送部分内容的罕见情况，或流式错误发送后的清理
             res.end();
        }
    }
});

// --- Helper: 获取当前文本 (v2.14 - 获取原始文本) ---
async function getRawTextContent(responseElement, previousText, reqId) {
    try {
         await responseElement.waitFor({ state: 'attached', timeout: 1500 });
         const preElement = responseElement.locator('pre').last();
         let rawText = null;
         try {
              await preElement.waitFor({ state: 'attached', timeout: 500 });
              rawText = await preElement.textContent({ timeout: 1000 });
         } catch {
              rawText = await responseElement.textContent({ timeout: 2000 });
         }
         return rawText !== null ? rawText.trim() : previousText;
    } catch (e) {
         // console.warn(`[${reqId}] (Warn) getRawTextContent failed: ${e.message.split('\n')[0]}. Retrying or returning previous.`);
         return previousText;
    }
}

// --- Helper: 发送流式块 ---
function sendStreamChunk(res, delta, reqId) {
    if (delta && !res.writableEnded) {
        const chunk = {
            id: `${CHAT_COMPLETION_ID_PREFIX}${Date.now()}-${Math.random().toString(36).substring(2, 15)}`,
            object: "chat.completion.chunk",
            created: Math.floor(Date.now() / 1000),
            model: MODEL_NAME,
            choices: [{ index: 0, delta: { content: delta }, finish_reason: null }]
        };
         try {
             res.write(`data: ${JSON.stringify(chunk)}\n\n`);
         } catch (writeError) {
              console.error(`[${reqId}] Error writing stream chunk:`, writeError.message);
              if (!res.writableEnded) res.end(); // End stream on write error
         }
    }
}

// --- Helper: 发送流式错误块 ---
function sendStreamError(res, errorMessage, reqId) {
     if (!res.writableEnded) {
         const errorPayload = { error: { message: `[${reqId}] Server error during streaming: ${errorMessage}`, type: 'server_error' } };
         try {
              // Avoid writing multiple DONE messages if error occurs after normal DONE
              if (!res.writableEnded) res.write(`data: ${JSON.stringify(errorPayload)}\n\n`);
              if (!res.writableEnded) res.write('data: [DONE]\n\n');
         } catch (e) {
             console.error(`[${reqId}] Error writing stream error chunk:`, e.message);
         } finally {
             if (!res.writableEnded) res.end(); // Ensure stream ends
         }
     }
}

// --- Helper: 保存错误快照 ---
async function saveErrorSnapshot(errorName = 'error') {
     // Extract reqId if present in the name
     const nameParts = errorName.split('_');
     const reqId = nameParts[nameParts.length - 1].length === 7 ? nameParts.pop() : null; // Simple check for likely reqId
     const baseErrorName = nameParts.join('_');
     const logPrefix = reqId ? `[${reqId}]` : '[No ReqId]';

     if (!browser?.isConnected() || !page || page.isClosed()) {
         console.log(`${logPrefix} 无法保存错误快照 (${baseErrorName})，浏览器或页面不可用。`);
         return;
     }
     console.log(`${logPrefix} 尝试保存错误快照 (${baseErrorName})...`);
     const timestamp = Date.now();
     const errorDir = path.join(__dirname, 'errors');
     try {
          if (!fs.existsSync(errorDir)) fs.mkdirSync(errorDir, { recursive: true });
          // Include reqId in filename if available
          const filenameSuffix = reqId ? `${reqId}_${timestamp}` : `${timestamp}`;
          const screenshotPath = path.join(errorDir, `${baseErrorName}_screenshot_${filenameSuffix}.png`);
          const htmlPath = path.join(errorDir, `${baseErrorName}_page_${filenameSuffix}.html`);

          try {
               await page.screenshot({ path: screenshotPath, fullPage: true, timeout: 15000 });
               console.log(`${logPrefix}    错误快照已保存到: ${screenshotPath}`);
          } catch (screenshotError) {
               console.error(`${logPrefix}    保存屏幕截图失败 (${baseErrorName}): ${screenshotError.message}`);
          }
          try {
               const content = await page.content({timeout: 15000});
               fs.writeFileSync(htmlPath, content);
               console.log(`${logPrefix}    错误页面HTML已保存到: ${htmlPath}`);
          } catch (htmlError) {
                console.error(`${logPrefix}    保存页面HTML失败 (${baseErrorName}): ${htmlError.message}`);
          }
     } catch (dirError) {
          console.error(`${logPrefix}    创建错误目录或保存快照时出错: ${dirError.message}`);
     }
}

// v2.14: Helper to safely parse JSON, attempting to find the outermost object/array
function tryParseJson(text, reqId) {
    if (!text || typeof text !== 'string') return null;
    text = text.trim();

    let startIndex = -1;
    let endIndex = -1;

    const firstBrace = text.indexOf('{');
    const firstBracket = text.indexOf('[');

    if (firstBrace !== -1 && (firstBracket === -1 || firstBrace < firstBracket)) {
        startIndex = firstBrace;
        endIndex = text.lastIndexOf('}');
    } else if (firstBracket !== -1) {
        startIndex = firstBracket;
        endIndex = text.lastIndexOf(']');
    }

    if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
        // console.warn(`[${reqId}] (Warn) Could not find valid start/end braces/brackets for JSON parsing.`);
        return null;
    }

    const jsonText = text.substring(startIndex, endIndex + 1);

    try {
        return JSON.parse(jsonText);
    } catch (e) {
         // console.warn(`[${reqId}] (Warn) JSON parse failed for extracted text: ${e.message}`);
        return null;
    }
}

// --- Helper: 检测并提取页面错误提示 ---
async function detectAndExtractPageError(page, reqId) {
    const errorToastLocator = page.locator(ERROR_TOAST_SELECTOR).last();
    try {
        const isVisible = await errorToastLocator.isVisible({ timeout: 1000 });
        if (isVisible) {
            console.log(`[${reqId}]    检测到错误 Toast 元素。`);
            const messageLocator = errorToastLocator.locator('span.content-text');
            const errorMessage = await messageLocator.textContent({ timeout: 500 });
            return errorMessage || "Detected error toast, but couldn't extract specific message.";
        } else {
             return null;
        }
    } catch (e) {
        // console.warn(`[${reqId}] (Warn) Checking for error toast failed or timed out: ${e.message.split('\n')[0]}`);
        return null;
    }
}

// --- Helper: 快速检查结束条件 ---
async function checkEndConditionQuickly(page, spinnerLocator, inputLocator, buttonLocator, timeoutMs = 250, reqId) {
    try {
        const results = await Promise.allSettled([
            expect(spinnerLocator).toBeHidden({ timeout: timeoutMs }),
            expect(inputLocator).toHaveValue('', { timeout: timeoutMs }),
            expect(buttonLocator).toBeDisabled({ timeout: timeoutMs })
        ]);
        const allMet = results.every(result => result.status === 'fulfilled');
        // console.log(`[${reqId}] (Quick Check) All met: ${allMet}`);
        return allMet;
    } catch (error) {
        // console.warn(`[${reqId}] (Quick Check) Error during checkEndConditionQuickly: ${error.message}`);
        return false;
    }
}

// --- 启动服务器 ---
let serverInstance = null;
(async () => {
    await initializePlaywright();

    serverInstance = app.listen(SERVER_PORT, () => {
        console.log(`\n🚀 OpenAI API 代理服务器(v2.16 - 支持系统提示词 & 增加超时)正在监听 http://localhost:${SERVER_PORT}`); // Version bump
        console.log(`   - 访问 http://localhost:${SERVER_PORT}/ 可打开 Web UI 进行测试`);
        if (isPlaywrightReady) {
            console.log('✅ Playwright 已连接，服务器准备就绪。');
        } else {
            console.warn('⚠️ Playwright 未能成功初始化。API 请求将失败，直到连接成功。');
            console.warn('   请检查 Chrome 和 auto_connect_aistudio.js 的运行状态，或稍后重试 API 请求。');
        }
        console.log(`确保 Chrome (由 auto_connect_aistudio.js 启动并监听端口 ${CHROME_DEBUGGING_PORT}) 正在运行...`);
    });

    serverInstance.on('error', (error) => {
        if (error.code === 'EADDRINUSE') {
            console.error(`❌ 错误：端口 ${SERVER_PORT} 已被占用。请关闭使用该端口的程序或更改 SERVER_PORT 配置。`);
        } else {
            console.error('❌ 服务器启动失败:', error);
        }
        process.exit(1);
    });

})();

// --- 优雅关闭处理 ---
let isShuttingDown = false;
async function shutdown(signal) {
    if (isShuttingDown) return;
    isShuttingDown = true;
    console.log(`\n收到 ${signal} 信号，正在关闭服务器...`);

    if (serverInstance) {
        serverInstance.close(async (err) => {
            if (err) console.error("关闭 HTTP 服务器时出错:", err);
            else console.log("HTTP 服务器已关闭。");

            console.log("Playwright connectOverCDP 将自动断开。");
            // No need to explicitly disconnect browser in connectOverCDP mode
            console.log('服务器优雅关闭完成。');
            process.exit(err ? 1 : 0);
        });

        // Force exit after timeout
        setTimeout(() => {
            console.error("优雅关闭超时，强制退出进程。");
            process.exit(1);
        }, 10000); // 10 seconds timeout
    } else {
        console.log("服务器实例未找到，直接退出。");
        process.exit(0);
    }
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM')); 