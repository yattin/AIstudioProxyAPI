// server_refactored.cjs (v2.30 - 统一获取逻辑)

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
const SERVER_PORT = process.env.PORT || 2048;
const CHROME_DEBUGGING_PORT = 8848;
const CDP_ADDRESS = `http://127.0.0.1:${CHROME_DEBUGGING_PORT}`;
const AI_STUDIO_URL_PATTERN = 'aistudio.google.com/';
const RESPONSE_COMPLETION_TIMEOUT = 300000; // 5分钟总超时
const POLLING_INTERVAL = 500; // 通用检查间隔 (ms)
const SPINNER_CHECK_TIMEOUT_MS = 1000; // 检查Spinner状态的超时
const FINAL_STATE_CHECK_TIMEOUT_MS = 1500; // 检查按钮和输入框最终状态的超时
const POST_COMPLETION_BUFFER = 1000; // 最终状态确认前的等待时间 (ms)
const SILENCE_TIMEOUT_MS = 1500; // 文本静默多久后认为稳定 (ms)

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

// --- Prompt 准备函数 ---
const prepareAIStudioPrompt = (userPrompt, systemPrompt = null) => {
    let fullPrompt = `\\nIMPORTANT: Your entire response MUST be a single JSON object. Do not include any text outside of this JSON object.\\nThe JSON object must have a single key named "response".\\nInside the value of the "response" key (which is a string), place your complete answer directly.\\n`;

    if (systemPrompt && systemPrompt.trim() !== '') {
        fullPrompt += `\\nSystem Instruction: ${systemPrompt}\\n`;
    }

    fullPrompt += `\\nExample 1:\\nUser asks: "What is the capital of France?"\\nYour response MUST be:\\n{\\n  "response": "The capital of France is Paris."\\n}\\n\\nExample 2:\\nUser asks: "Write a python function to add two numbers"\\nYour response MUST be:\\n{\\n  "response": "\\\`\\\`\\\`python\\ndef add(a, b):\\n  return a + b\\n\\\`\\\`\\\`"\\n}\\n\\nNow, answer the following user prompt, ensuring your output strictly adheres to the JSON format described above:\\n\\nUser Prompt: "${userPrompt}"\\n\\nYour JSON Response:\\n`;
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

// --- API 辅助函数 ---

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
    const loadingSpinner = page.locator(LOADING_SPINNER_SELECTOR);

    console.log(`[${reqId}]  - 等待输入框可用...`);
    try {
        await inputField.waitFor({ state: 'visible', timeout: 10000 });
    } catch (e) {
        console.error(`[${reqId}] ❌ 查找输入框失败！`);
        await saveErrorSnapshot(`input_field_not_visible_${reqId}`);
        throw new Error(`[${reqId}] Failed to find visible input field. Error: ${e.message}`);
    }

    console.log(`[${reqId}]  - 清空并填充输入框...`);
    try {
        await inputField.fill(prompt, { timeout: 10000 });
    } catch (e) {
        console.error(`[${reqId}] ❌ 填充输入框失败！`);
        await saveErrorSnapshot(`input_fill_fail_${reqId}`);
        throw new Error(`[${reqId}] Failed to fill input field. Error: ${e.message}`);
    }


    console.log(`[${reqId}]  - 等待运行按钮可用...`);
    try {
        await expect(submitButton).toBeEnabled({ timeout: 10000 });
    } catch (e) {
        console.error(`[${reqId}] ❌ 等待运行按钮变为可用状态超时！`);
        await saveErrorSnapshot(`submit_button_not_enabled_before_click_${reqId}`);
        throw new Error(`[${reqId}] Submit button not enabled before click. Error: ${e.message}`);
    }

    console.log(`[${reqId}]  - 点击运行按钮...`);
    try {
        await submitButton.click({ timeout: 10000 });
    } catch (e) {
        console.error(`[${reqId}] ❌ 点击运行按钮失败！`);
        await saveErrorSnapshot(`submit_button_click_fail_${reqId}`);
        throw new Error(`[${reqId}] Failed to click submit button. Error: ${e.message}`);
    }


    return { inputField, submitButton, loadingSpinner }; // Return locators
}

// 定位 AI 回复元素
async function locateResponseElements(page, locators /* Pass locators */, reqId) {
    // console.log(`[${reqId}] 定位 AI 回复元素...`); // Commented out noisy log
    let lastResponseContainer;
    let responseElement;
    let locatedResponseElements = false;

    // Increased retries for robustness
    for (let i = 0; i < 3 && !locatedResponseElements; i++) {
        try {
            // console.log(`[${reqId}]    (Locate Attempt ${i + 1}) 尝试定位最新回复容器及文本元素...`); // Comment out loop log
            await page.waitForTimeout(500 + i * 300); // Slightly longer initial delay and increment

            lastResponseContainer = page.locator(RESPONSE_CONTAINER_SELECTOR).last();
            await lastResponseContainer.waitFor({ state: 'attached', timeout: 7000 }); // Increased timeout

            responseElement = lastResponseContainer.locator(RESPONSE_TEXT_SELECTOR);
            await responseElement.waitFor({ state: 'attached', timeout: 7000 }); // Increased timeout

            // console.log(`[${reqId}]    (Locate Attempt ${i + 1}) 回复容器和文本元素定位成功。`); // Comment out loop log
            locatedResponseElements = true;
        } catch (locateError) {
            console.warn(`[${reqId}]    (Locate Attempt ${i + 1}) 定位回复元素失败: ${locateError.message.split('\\n')[0]}`);
            if (i === 2) {
                console.error(`[${reqId}] ❌ 无法在多次尝试后定位响应元素。`);
                await saveErrorSnapshot(`locate_response_fail_final_${reqId}`); // Save snapshot on final failure
            }
        }
    }
    // Return nulls if not found, handled by caller
    return { responseElement: locatedResponseElements ? responseElement : null, lastResponseContainer: locatedResponseElements ? lastResponseContainer : null };
}

// 获取原始文本内容
async function getRawTextContent(responseElement, previousText, reqId) {
    if (!responseElement) {
         console.warn(`[${reqId}] (getRawTextContent) responseElement is null, returning previousText.`);
        return previousText;
    }
    try {
         // Ensure the element is somewhat stable before trying to get text
         await responseElement.waitFor({ state: 'visible', timeout: 2500 }); // Wait for visible instead of just attached
         // Prioritize reading from <pre> if available, fallback to whole node
         const preElement = responseElement.locator('pre').last();
         let rawText = null;
         try {
              await preElement.waitFor({ state: 'attached', timeout: 1000 }); // Shorter timeout for pre
              rawText = await preElement.textContent({ timeout: 1500 });
         } catch {
              rawText = await responseElement.textContent({ timeout: 2500 });
         }
         // Normalize whitespace and trim
         return rawText !== null ? rawText.replace(/\\s+/g, ' ').trim() : previousText;
    } catch (e) {
         console.warn(`[${reqId}] (Warn) getRawTextContent failed: ${e.message.split('\\n')[0]}. Returning previous.`);
         return previousText;
    }
}


// --- 处理 /v1/models 请求 ---
app.get('/v1/models', (req, res) => {
    const modelId = 'aistudio-proxy';
    const logPrefix = `[${Date.now().toString(36).slice(-5)}]`;
    console.log(`${logPrefix} --- 收到 /v1/models 请求，返回模拟模型列表 ---`);
    res.json({
        object: "list",
        data: [
            {
                id: modelId,
                object: "model",
                created: Math.floor(Date.now() / 1000),
                owned_by: "openai-proxy",
                permission: [],
                root: modelId,
                parent: null
            }
        ]
    });
});


// --- API 端点 (重构 v2.30 - 统一获取逻辑) ---
app.post('/v1/chat/completions', async (req, res) => {
    const reqId = Math.random().toString(36).substring(2, 9); // 生成简短的请求 ID
    console.log(`\\n[${reqId}] --- 收到 /v1/chat/completions 请求 ---`);

    // 1. 检查 Playwright 状态
    if (!isPlaywrightReady && !isInitializing) {
        console.warn(`[${reqId}] Playwright 未就绪，尝试重新初始化...`);
        await initializePlaywright();
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
        console.log(`[${reqId}] 请求模式: ${isStreaming ? '流式 (模拟)' : '非流式 (JSON)'}`);

        // 2. 设置总操作超时
        operationTimer = setTimeout(async () => {
            await saveErrorSnapshot(`operation_timeout_${reqId}`);
            console.error(`[${reqId}] Operation timed out after ${RESPONSE_COMPLETION_TIMEOUT / 1000} seconds.`);
            if (!res.headersSent) {
                res.status(504).json({ error: { message: `[${reqId}] Operation timed out`, type: 'timeout_error' } });
            } else if (isStreaming && !res.writableEnded) {
                sendStreamError(res, "Operation timed out on server.", reqId);
            } else if (!res.writableEnded){
                 res.end();
            }
        }, RESPONSE_COMPLETION_TIMEOUT);

        // 3. 验证请求
        const { userPrompt, systemPrompt: extractedSystemPrompt } = validateChatRequest(messages);
        const systemPrompt = extractedSystemPrompt || otherParams?.system_prompt;

        console.log(`[${reqId}]   原始 User Prompt (start): "${userPrompt?.substring(0, 80)}..."`);
        if (systemPrompt) {
            console.log(`[${reqId}]   System Prompt (start): "${systemPrompt.substring(0, 80)}..."`);
        }
        if (Object.keys(otherParams).length > 0) {
            console.log(`[${reqId}]   记录到的额外参数: ${JSON.stringify(otherParams)}`);
        }

        // 4. 准备 Prompt
        const prompt = prepareAIStudioPrompt(userPrompt, systemPrompt);
        console.log(`[${reqId}] 构建的 Prompt (JSON): "${prompt.substring(0, 200)}..."`);

        // 5. 与页面交互并提交
        const locators = await interactAndSubmitPrompt(page, prompt, reqId);

        // --- Conditional logic based on streaming ---
        if (isStreaming) {
            // --- START: Rewritten Real-time Streaming Logic (v3 - Hybrid) ---
            console.log(`[${reqId}] Sending real-time streaming response (Rewritten v3)...`);
            if (!res.headersSent) {
                res.writeHead(200, {
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                });
            }

            let state = 'INITIAL'; // Use simple state: INITIAL, STREAMING, FINISHED
            let previousValue = '';  // Store the last successfully sent value part
            const POLLING_INTERVAL_MS = 100;
            let monitoringInterval = null;
            const marker = '{"response": "';

            try {
                // Initial locator check remains the same
                if (!locators || !locators.inputField) {
                     throw new Error("Locators object is invalid after prompt submission.");
                }

                monitoringInterval = setInterval(async () => {
                    // --- Outer try-catch for interval callback ---
                    try {
                        if (state === 'FINISHED' || res.writableEnded) {
                            clearInterval(monitoringInterval);
                            return;
                        }

                        // --- Re-locate response element --- 
                        let currentResponseElement;
                        try {
                            const located = await locateResponseElements(page, locators, reqId);
                            if (!located || !located.responseElement) {
                                // console.warn(`[${reqId}] ⚠️ Response element not found in interval. Skipping.`);
                                return; 
                            }
                            currentResponseElement = located.responseElement;
                        } catch (locateErrorInLoop) {
                             console.warn(`[${reqId}] ⚠️ Error locating response element: ${locateErrorInLoop.message.split('\\n')[0]}. Skipping.`);
                             return; 
                        } 

                        // --- Fetch text content (with fallback) --- 
                        let currentRawText = null;
                        try {
                            const preElement = currentResponseElement.locator('pre').last();
                            currentRawText = await preElement.textContent({ timeout: 1000 })
                                .catch(preReadError => null);
                            if (currentRawText === null) {
                                currentRawText = await currentResponseElement.textContent({ timeout: 2000 })
                                    .catch(textReadError => {
                                        console.warn(`[${reqId}] ⚠️ Error reading textContent (fallback): ${textReadError.message.split('\\n')[0]}. Skipping.`);
                                        return null;
                                    });
                            }
                        } catch (locatePreError) {
                             currentRawText = await currentResponseElement.textContent({ timeout: 2000 })
                                 .catch(textReadError => {
                                     console.warn(`[${reqId}] ⚠️ Error reading textContent (fallback after pre locate error): ${textReadError.message.split('\\n')[0]}. Skipping.`);
                                     return null;
                                 });
                        }

                        if (currentRawText === null) {
                           return; // Skip if text read failed
                        }
                        
                        // --- Process based on state --- 
                        let currentValue = ''; // Value extracted in this iteration
                        
                        if (state === 'INITIAL') {
                            const startIndex = currentRawText.indexOf(marker);
                            if (startIndex !== -1) {
                                console.log(`[${reqId}] Found JSON start marker. Switching to STREAMING state.`);
                                state = 'STREAMING';
                                previousValue = ''; // Reset previous value
                                // Re-process the same text in STREAMING state immediately
                            } else {
                                // console.log(`[${reqId}] Still looking for start marker...`);
                                // Optional: log raw text if debugging needed
                                // console.log(`[${reqId}] Raw: "${currentRawText.substring(0, 70).replace(/\n/g, '\\n')}..."`);
                            }
                        }
                        
                        // Use 'if' not 'else if' to allow immediate processing after state change
                        if (state === 'STREAMING') { 
                            const startIndex = currentRawText.indexOf(marker);
                            if (startIndex === -1) {
                                // Marker disappeared after being found!
                                console.warn(`[${reqId}] ⚠️ JSON start marker disappeared. Resetting to INITIAL state.`);
                                state = 'INITIAL';
                                previousValue = '';
                                return; // Skip further processing this interval
                            }

                            const potentialValueString = currentRawText.substring(startIndex + marker.length);
                            
                            // Find the first non-escaped closing quote to delimit the current value
                            let endQuoteIndex = -1;
                            let searchPos = 0;
                            while(searchPos < potentialValueString.length) {
                                let quotePos = potentialValueString.indexOf('"', searchPos);
                                if (quotePos === -1) break; 
                                if (quotePos > 0 && potentialValueString[quotePos - 1] === '\\\\') {
                                    searchPos = quotePos + 1;
                                } else {
                                    endQuoteIndex = quotePos;
                                    break;
                                }
                            }

                            if (endQuoteIndex !== -1) {
                                // Found a potential end quote
                                currentValue = potentialValueString.substring(0, endQuoteIndex);
                            } else {
                                // No end quote yet, assume everything after marker is value
                                currentValue = potentialValueString;
                            }

                            // Calculate and send increment
                            if (currentValue.length > previousValue.length) {
                                const increment = currentValue.substring(previousValue.length);
                                // console.log(`[${reqId}] Sending increment (len: ${increment.length}): "${increment.substring(0, 50).replace(/\n/g, '\\n')}..."`); // REMOVED: Keep logs minimal
                                sendStreamChunk(res, increment, reqId);
                                previousValue = currentValue;
                            } else if (currentValue !== previousValue) {
                                // Handle content change without length increase (or shrinking)
                                console.warn(`[${reqId}] ⚠️ Value changed without increasing length (or shrank). Sending full value.`);
                                sendStreamChunk(res, currentValue, reqId);
                                previousValue = currentValue;
                            }
                            // No else needed if value is identical
                        } // end if (state === 'STREAMING')
                        
                        // --- UI Fallback Check (Simplified) --- 
                        if (state === 'STREAMING') { // Only check UI if we think we are streaming
                             try { 
                                const isSpinnerHidden = await locators.loadingSpinner.isHidden({ timeout: 150 });
                                const isButtonDisabled = await locators.submitButton.isDisabled({ timeout: 150 });
                                if (isSpinnerHidden && isButtonDisabled) { 
                                    console.log(`[${reqId}] UI indicates completion. Finalizing stream.`);
                                    state = 'FINISHED';
                                    clearInterval(monitoringInterval);
                                    clearTimeout(operationTimer);
                                    // console.log(`[${reqId}] Finalizing stream via UI fallback. Last value: "${previousValue.substring(0,100).replace(/\n/g, '\\n')}..."`); // REMOVED: Keep logs minimal
                                    if (!res.writableEnded) { res.write('data: [DONE]\\n\\n'); res.end(); }
                                }
                            } catch (uiCheckError) {
                                // console.warn(`[${reqId}] ⚠️ Warning during fallback UI check: ${uiCheckError.message.split('\\n')[0]}`); // REMOVED: Keep logs minimal
                            } 
                        } 
                        
                    // --- Outer catch for the entire interval callback ---
                    } catch (intervalError) { 
                        console.error(`[${reqId}] ❌ Error inside monitoring interval: ${intervalError.message}`);
                        state = 'FINISHED'; 
                        clearInterval(monitoringInterval);
                        clearTimeout(operationTimer); 
                        sendStreamError(res, `Error during streaming monitor: ${intervalError.message}`, reqId);
                        if (!res.writableEnded) res.end();
                    } 
                }, POLLING_INTERVAL_MS); 

                // Timeout handler - Log previousValue
                operationTimer._onTimeout = () => { 
                    console.error(`[${reqId}] Streaming operation timed out (${RESPONSE_COMPLETION_TIMEOUT}ms).`);
                    if (monitoringInterval) clearInterval(monitoringInterval);
                     console.log(`[${reqId}] Finalizing stream via TIMEOUT. Last known value: "${previousValue.substring(0, 100).replace(/\n/g, '\\n')}..."`); // Keep this debug log
                    if (state !== 'FINISHED' && !res.writableEnded) {
                        sendStreamError(res, "Operation timed out on server.", reqId);
                    } else if (!res.writableEnded){
                         res.end();
                    }
                };
                // --- END: Rewritten Real-time Streaming Logic (Setup part) ---

            } catch (streamingSetupError) { 
                 console.error(`[${reqId}] ❌ Error setting up streaming: ${streamingSetupError.message}`);
                 clearTimeout(operationTimer); 
                 if (monitoringInterval) clearInterval(monitoringInterval); 
                 sendStreamError(res, `Failed to setup streaming: ${streamingSetupError.message}`, reqId);
                 if (!res.writableEnded) res.end();
            } 

        } else {
            // --- Non-Streaming Logic (Remains the same) ---
            console.log(`[${reqId}] Waiting for AI completion (non-streaming)...`);
            const completionConfirmed = await waitForAICompletion(page, locators, reqId);
            if (!completionConfirmed) {
                 console.warn(`[${reqId}] AI completion confirmation timed out, attempting to get content anyway.`);
            }

            console.log(`[${reqId}] Getting and processing final response (non-streaming)...`);
            const finalContent = await getAndProcessFinalResponse(page, locators, reqId);
            console.log(`[${reqId}] ✅ Retrieved final content (length: ${finalContent?.length}).`);

            const finalPageError = await detectAndExtractPageError(page, reqId);
            if (finalPageError) {
                console.error(`[${reqId}] ❌ Post-processing AI Studio page error: ${finalPageError}`);
                await saveErrorSnapshot(`page_error_post_processing_${reqId}`);
                if (!finalContent) {
                     throw new Error(`[${reqId}] AI Studio Error detected, and no content retrieved: ${finalPageError}`);
                } else {
                     console.warn(`[${reqId}] AI Studio error detected, proceeding with potentially incomplete content.`);
                }
            }

            clearTimeout(operationTimer);
            const responsePayload = {
                id: `${CHAT_COMPLETION_ID_PREFIX}${Date.now()}-${Math.random().toString(36).substring(2, 15)}`,
                object: 'chat.completion',
                created: Math.floor(Date.now() / 1000),
                model: MODEL_NAME,
                choices: [{
                    index: 0,
                    message: { role: 'assistant', content: finalContent || "Error: Failed to retrieve content." },
                    finish_reason: 'stop',
                }],
                usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
            };
            console.log(`[${reqId}] ✅ Sending JSON response.`);
            res.json(responsePayload);
        } // End if(isStreaming)/else

    } catch (error) { // Catch errors in the main handler
        console.error(`[${reqId}] ❌ Top-level API request error: ${error.message}\n${error.stack}`);
        if (monitoringInterval) clearInterval(monitoringInterval); // Ensure interval cleared on any error
        clearTimeout(operationTimer); // Ensure main timer cleared

        if (!error.message?.includes('snapshot') && !error.stack?.includes('saveErrorSnapshot')) {
             await saveErrorSnapshot(`general_api_error_${reqId}`);
        }

        // Send error response
        if (!res.headersSent) {
             let statusCode = 500;
             let errorType = 'server_error';
             if (error.message?.includes('timed out') || error.message?.includes('timeout')) {
                 statusCode = 504; errorType = 'timeout_error';
             } else if (error.message?.includes('AI Studio Error')) {
                 statusCode = 502; errorType = 'upstream_error';
             } else if (error.message?.includes('Invalid request')) {
                 statusCode = 400; errorType = 'invalid_request_error';
             } else if (error.message?.includes('locate AI response element') || error.message?.includes('streaming setup')) {
                 statusCode = 503; errorType = 'server_error';
             }
            res.status(statusCode).json({ error: { message: `[${reqId}] ${error.message}`, type: errorType } });
        } else if (isStreaming && !res.writableEnded) {
             sendStreamError(res, error.message, reqId);
        } else if (!res.writableEnded) {
             res.end();
        }
    } finally { // Main handler finally block
         clearTimeout(operationTimer); // Final check to clear timer
         // Interval should be cleared within its specific logic paths or catch blocks
    } // End finally
}); // End app.post

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
              if (!res.writableEnded) res.end();
         }
    }
}

// --- Helper: 发送流式错误块 ---
function sendStreamError(res, errorMessage, reqId) {
     if (!res.writableEnded) {
         const errorPayload = { error: { message: `[${reqId}] Server error during streaming: ${errorMessage}`, type: 'server_error' } };
         try {
              if (!res.writableEnded) res.write(`data: ${JSON.stringify(errorPayload)}\n\n`);
              if (!res.writableEnded) res.write('data: [DONE]\n\n');
         } catch (e) {
             console.error(`[${reqId}] Error writing stream error chunk:`, e.message);
         } finally {
             if (!res.writableEnded) res.end();
         }
     }
}

// --- Helper: 保存错误快照 ---
async function saveErrorSnapshot(errorName = 'error') {
     const nameParts = errorName.split('_');
     const reqId = nameParts[nameParts.length - 1].length === 7 ? nameParts.pop() : null;
     const baseErrorName = nameParts.join('_');
     const logPrefix = reqId ? `[${reqId}]` : '[No ReqId]';

     if (!browser?.isConnected() || !page || page.isClosed()) {
         console.log(`${logPrefix} 无法保存错误快照 (${baseErrorName})，浏览器或页面不可用.`);
         return;
     }
     console.log(`${logPrefix} 尝试保存错误快照 (${baseErrorName})...`);
     const timestamp = Date.now();
     const errorDir = path.join(__dirname, 'errors');
     try {
          if (!fs.existsSync(errorDir)) fs.mkdirSync(errorDir, { recursive: true });
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

// --- Helper: 获取并处理最终响应 ---
async function getAndProcessFinalResponse(page, locators, reqId) {
    console.log(`[${reqId}]   - 开始获取并解析最终响应...`);
    let aiResponseText = null;
    const maxRetries = 3;
    let attempts = 0;
    let rawText = '';

    while (attempts < maxRetries && aiResponseText === null) {
        attempts++;
        console.log(`[${reqId}]     - 尝试定位并获取原始文本 (第 ${attempts} 次)...`);
        try {
            const { responseElement: currentResponseElement } = await locateResponseElements(page, locators, reqId);

            if (!currentResponseElement) {
                 console.warn(`[${reqId}]     - 第 ${attempts} 次未能定位到响应元素。`);
                 throw new Error("Failed to locate response element.");
            }

            rawText = await getRawTextContent(currentResponseElement, '', reqId);

            if (!rawText || rawText.trim() === '') {
                console.warn(`[${reqId}]     - 第 ${attempts} 次获取的原始文本为空。`);
                if (attempts < maxRetries) {
                     await page.waitForTimeout(1000 + attempts * 500);
                     continue;
                } else {
                     throw new Error("Raw text content is empty after multiple attempts.");
                }
            }
            console.log(`[${reqId}]     - 获取到原始文本 (长度: ${rawText.length}): "${rawText.substring(0, 150)}..."`);

            const parsedJson = tryParseJson(rawText, reqId);

            if (parsedJson) {
                if (typeof parsedJson.response === 'string') {
                    aiResponseText = parsedJson.response;
                    console.log(`[${reqId}]     - 成功解析 JSON 并提取 'response' 字段。`);
                } else {
                    try {
                        aiResponseText = JSON.stringify(parsedJson);
                        console.log(`[${reqId}]     - 警告: 未找到 'response' 字段，但解析到有效 JSON。将整个 JSON 字符串化作为回复。`);
                    } catch (stringifyError) {
                        console.error(`[${reqId}]     - 错误：无法将解析出的 JSON 字符串化: ${stringifyError.message}`);
                        throw new Error("Failed to stringify the parsed JSON object.");
                    }
                }
            } else {
                console.warn(`[${reqId}]     - 未能从原始文本中解析出 JSON。将使用原始文本作为基础。`);
                aiResponseText = rawText;
            }
            break;

        } catch (e) {
            console.warn(`[${reqId}]     - 第 ${attempts} 次获取或处理失败: ${e.message.split('\\n')[0]}`);
            aiResponseText = null;
            if (attempts >= maxRetries) {
                console.error(`[${reqId}] ❌ 多次尝试获取并处理响应文本失败。`);
                await saveErrorSnapshot(`get_process_response_failed_final_${reqId}`);
                return ""; // Return empty string on complete failure
            } else {
                await new Promise(resolve => setTimeout(resolve, 1500 + attempts * 500));
            }
        }
    }

    if (aiResponseText === null) {
         console.error(`[${reqId}] 最终未能获取到任何响应文本。`);
         return "";
    }

    // --- 清理最终响应文本 ---
    let finalContentForUser = aiResponseText;

    // 1. 处理可能的嵌套 JSON
    try {
         if (finalContentForUser && (finalContentForUser.startsWith('{') || finalContentForUser.startsWith('[')) && finalContentForUser.length > 2 ) {
             const outerParsed = JSON.parse(finalContentForUser);
             if (typeof outerParsed.response === 'string') {
                 const innerParsed = tryParseJson(outerParsed.response, reqId);
                 if (innerParsed && typeof innerParsed.response === 'string') {
                    console.log(`[${reqId}]    (Cleanup) 检测到双重嵌套 JSON，提取最内层 response.`);
                     finalContentForUser = innerParsed.response;
                 } else {
                    console.log(`[${reqId}]    (Cleanup) 使用外层 'response' 字段内容.`);
                     finalContentForUser = outerParsed.response;
                 }
             }
         }
    } catch (e) { /* Keep finalContentForUser as is */ }

    // 2. 移除开始标记
    const startMarker = '<<<START_RESPONSE>>>';
    if (finalContentForUser && finalContentForUser.startsWith(startMarker)) {
        finalContentForUser = finalContentForUser.substring(startMarker.length);
        console.log(`[${reqId}]    (Cleanup) 移除了前缀 ${startMarker}.`);
    } else {
         if (rawText === finalContentForUser) {
              console.warn(`[${reqId}]    (Cleanup) 警告: 未在最终内容中找到预期的 ${startMarker} 前缀 (可能因为原始回复不是预期JSON格式).`);
         }
    }

    // 3. Final trim
    finalContentForUser = finalContentForUser.trim();

    console.log(`[${reqId}]   - 清理后的最终内容 (长度: ${finalContentForUser.length}): "${finalContentForUser.substring(0, 100)}..."`);
    return finalContentForUser;
}


// --- Helper: 安全解析 JSON (modified slightly for clarity) ---
function tryParseJson(text, reqId) {
    if (!text || typeof text !== 'string') return null;
    const trimmedText = text.trim(); // Trim whitespace first

    let startIndex = -1;
    let endIndex = -1;

    // Find the first opening brace or bracket
    const firstBrace = trimmedText.indexOf('{');
    const firstBracket = trimmedText.indexOf('[');

    if (firstBrace !== -1 && (firstBracket === -1 || firstBrace < firstBracket)) {
        startIndex = firstBrace;
        // Find the last closing brace for robustness against nested objects/errors
        endIndex = trimmedText.lastIndexOf('}');
    } else if (firstBracket !== -1) {
        startIndex = firstBracket;
        // Find the last closing bracket
        endIndex = trimmedText.lastIndexOf(']');
    }

    if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
        // console.log(`[${reqId}] (tryParseJson) No valid start/end brackets/braces found in: "${trimmedText.substring(0, 50)}..."`);
        return null; // No valid structure found
    }

    // Extract the potential JSON part
    const jsonText = trimmedText.substring(startIndex, endIndex + 1);

    try {
        return JSON.parse(jsonText);
    } catch (e) {
         // console.warn(`[${reqId}] (tryParseJson) Failed to parse extracted text: "${jsonText.substring(0,100)}...". Error: ${e.message}`);
        return null; // Parsing failed
    }
}

// --- Helper: 检测并提取页面错误提示 ---
async function detectAndExtractPageError(page, reqId) {
    const errorToastLocator = page.locator(ERROR_TOAST_SELECTOR).last();
    try {
        const isVisible = await errorToastLocator.isVisible({ timeout: 1000 });
        if (isVisible) {
            console.log(`[${reqId}]    检测到错误 Toast 元素.`);
            const messageLocator = errorToastLocator.locator('span.content-text');
            const errorMessage = await messageLocator.textContent({ timeout: 500 });
            return errorMessage || "Detected error toast, but couldn't extract specific message.";
        } else {
             return null;
        }
    } catch (e) {
        return null;
    }
}

// --- Helper: 等待 AI 完成 (UI 状态 + 文本静默检查) ---
async function waitForAICompletion(page, locators, reqId) {
    console.log(`[${reqId}]   - 等待 AI 处理完成 (UI 检查 + 文本静默)...`);
    const waitStartTime = Date.now();
    const { inputField, submitButton, loadingSpinner } = locators;
    let processComplete = false;
    let finalStateCheckInitiated = false;

    while (!processComplete && Date.now() - waitStartTime < RESPONSE_COMPLETION_TIMEOUT) {
        let isSpinnerHidden = false;
        let isInputEmpty = false;
        let isButtonDisabled = false;

        // --- 检查 UI 状态 ---
        try {
            await expect(loadingSpinner).toBeHidden({ timeout: SPINNER_CHECK_TIMEOUT_MS });
            isSpinnerHidden = true;

            try {
                await expect(inputField).toHaveValue('', { timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                isInputEmpty = true;
            } catch { /* Input not empty */ }

            try {
                await expect(submitButton).toBeDisabled({ timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                isButtonDisabled = true;
            } catch { /* Button not disabled */ }

        } catch { /* Spinner still visible or initial check failed */ }
        // --- 结束 UI 状态检查 ---


        if (isSpinnerHidden && isInputEmpty && isButtonDisabled) {
            // --- 潜在最终状态 ---
            if (!finalStateCheckInitiated) {
                finalStateCheckInitiated = true;
                console.log(`[${reqId}]    检测到潜在最终 UI 状态。等待 ${POST_COMPLETION_BUFFER}ms 进行确认...`);
                await page.waitForTimeout(POST_COMPLETION_BUFFER);
                console.log(`[${reqId}]    ${POST_COMPLETION_BUFFER}ms 等待结束，重新检查 UI 状态...`);
                try {
                    // 严格重检 UI
                    await expect(loadingSpinner).toBeHidden({ timeout: 500 });
                    await expect(inputField).toHaveValue('', { timeout: 500 });
                    await expect(submitButton).toBeDisabled({ timeout: 500 });
                    console.log(`[${reqId}]    UI 状态确认成功。开始文本静默检查...`);

                    // --- 文本静默检查 ---
                    let lastCheckText = '';
                    let currentCheckText = '';
                    let textStable = false;
                    let lastTextChangeTime = Date.now();

                    const { responseElement: checkResponseElement } = await locateResponseElements(page, locators, reqId);

                    if (checkResponseElement) {
                        currentCheckText = await getRawTextContent(checkResponseElement, '', reqId);
                        lastCheckText = currentCheckText;
                        console.log(`[${reqId}]    (静默检查) 初始文本长度: ${currentCheckText?.length}`);

                        const silenceCheckEndTime = Date.now() + SILENCE_TIMEOUT_MS;
                        while (Date.now() < silenceCheckEndTime) {
                            await page.waitForTimeout(POLLING_INTERVAL);
                            currentCheckText = await getRawTextContent(checkResponseElement, lastCheckText, reqId);

                            if (currentCheckText !== lastCheckText) {
                                console.log(`[${reqId}]    (静默检查) 文本仍在变化 (新长度: ${currentCheckText?.length}).`);
                                lastTextChangeTime = Date.now();
                                lastCheckText = currentCheckText;
                            } else {
                                if (Date.now() - lastTextChangeTime >= SILENCE_TIMEOUT_MS) {
                                    console.log(`[${reqId}]    文本内容稳定超过 ${SILENCE_TIMEOUT_MS}ms，确认处理完成.`);
                                    textStable = true;
                                    break;
                                }
                            }
                        }

                        if (!textStable) {
                             currentCheckText = await getRawTextContent(checkResponseElement, lastCheckText, reqId);
                             if (currentCheckText === lastCheckText && Date.now() - lastTextChangeTime >= SILENCE_TIMEOUT_MS) {
                                console.log(`[${reqId}]    (静默检查 - Post Loop) 文本内容最终确认稳定.`);
                                textStable = true;
                             }
                        }

                    } else {
                        console.warn(`[${reqId}]    (静默检查) 警告: 未能定位到回复元素，无法执行文本静默检查。将跳过.`);
                        textStable = true; // Assume stable if cannot locate
                    }

                    if (textStable) {
                        processComplete = true;
                        console.log(`[${reqId}] ✅ AI 处理完成 (UI 稳定 + 文本静默/无法检查).`);
                    } else {
                        console.warn(`[${reqId}]    警告: 文本静默检查超时 (${SILENCE_TIMEOUT_MS}ms)。将继续处理.`);
                        processComplete = true; // Proceed anyway
                        await saveErrorSnapshot(`wait_completion_silence_timeout_${reqId}`);
                    }
                    // --- 结束文本静默检查 ---

                } catch (recheckError) {
                    console.log(`[${reqId}]    UI 状态在确认期间发生变化 (${recheckError.message.split('\\n')[0]})。重置并继续轮询...`);
                    finalStateCheckInitiated = false;
                }
            } // End if (!finalStateCheckInitiated)
        } else {
            // --- UI 状态不满足 ---
            if (finalStateCheckInitiated) {
                console.log(`[${reqId}]    最终 UI 状态不再满足，重置确认标志.`);
                finalStateCheckInitiated = false;
            }
            await page.waitForTimeout(POLLING_INTERVAL);
        }
    }
    // --- 结束完成检查循环 ---

    if (!processComplete) {
        console.warn(`[${reqId}] 警告: 等待 AI 完成状态的总循环超时 (${RESPONSE_COMPLETION_TIMEOUT / 1000}s).`);
        await saveErrorSnapshot(`wait_completion_timeout_${reqId}`);
        return false; // Indicate completion wasn't confirmed
    }

    return true; // Indicate completion was confirmed
}

// --- 启动服务器 ---
let serverInstance = null;
(async () => {
    await initializePlaywright();

    serverInstance = app.listen(SERVER_PORT, () => {
        console.log(`\n🚀 OpenAI API 代理服务器(v2.30 - 统一获取逻辑)正在监听 http://localhost:${SERVER_PORT}`);
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
            console.log('服务器优雅关闭完成。');
            process.exit(err ? 1 : 0);
        });

        setTimeout(() => {
            console.error("优雅关闭超时，强制退出进程。");
            process.exit(1);
        }, 10000);
    } else {
        console.log("服务器实例未找到，直接退出。");
        process.exit(0);
    }
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM')); 