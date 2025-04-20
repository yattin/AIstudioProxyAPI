// server.cjs (优化版 v2.16 - 支持系统提示词 & 增加超时)

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

// v2.16: JSON Structure Prompt - Incorporates Optional System Prompt
const JSON_RESPONSE_PROMPT_TEMPLATE = (userPrompt, systemPrompt = null) => {
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

// --- API 端点 ---
app.post('/v1/chat/completions', async (req, res) => {
    if (!isPlaywrightReady && !isInitializing) {
        console.warn('Playwright 未就绪，尝试重新初始化...');
        await initializePlaywright();
    }

    if (!isPlaywrightReady || !page || page.isClosed() || !browser?.isConnected()) {
        console.error('API 请求失败：Playwright 未就绪、页面关闭或连接断开。');
         let detail = 'Unknown issue.';
         if (!browser?.isConnected()) detail = "Browser connection lost.";
         else if (!page || page.isClosed()) detail = "Target AI Studio page is not available or closed.";
         else if (!isPlaywrightReady) detail = "Playwright initialization failed or incomplete.";
        return res.status(503).json({
            error: { message: `Playwright connection is not active. ${detail} Please ensure Chrome is running correctly, the AI Studio tab is open, and potentially restart the server.`, type: 'server_error' }
        });
    }

    const { messages, stream, ...otherParams } = req.body;
    const userMessageContent = messages?.filter(msg => msg.role === 'user').pop()?.content;
    // v2.16: Extract potential system prompt from messages or otherParams
    const systemMessageContent = messages?.find(msg => msg.role === 'system')?.content || otherParams?.system_prompt;

    console.log(`\n--- 收到 /v1/chat/completions 请求 (Stream: ${stream === true}) ---`);
    console.log(`  原始 User Prompt (start): "${userMessageContent?.substring(0, 80)}..."`);
    if (systemMessageContent) {
        console.log(`  System Prompt (start): "${systemMessageContent.substring(0, 80)}..."`);
    }
    if (Object.keys(otherParams).length > 0) {
         console.log(`  记录到的额外参数: ${JSON.stringify(otherParams)}`);
    }

    const isStreaming = stream === true;
    if (isStreaming) {
        res.setHeader('Content-Type', 'text/event-stream');
        res.setHeader('Cache-Control', 'no-cache');
        res.setHeader('Connection', 'keep-alive');
        res.flushHeaders();
        console.log("模式: 流式 (SSE)");
    } else {
        console.log("模式: 非流式 (JSON)");
    }

    let operationTimer;

    try {
        operationTimer = setTimeout(async () => {
            await saveErrorSnapshot('operation_timeout');
            console.error(`Operation timed out after ${RESPONSE_COMPLETION_TIMEOUT / 1000} seconds.`);
            if (!res.headersSent) {
                 res.status(504).json({ error: { message: 'Operation timed out', type: 'timeout_error' } });
            } else if (isStreaming && !res.writableEnded) {
                 sendStreamError(res, "Operation timed out on server.");
            }
        }, RESPONSE_COMPLETION_TIMEOUT);

        if (!messages || !Array.isArray(messages) || messages.length === 0) {
             throw new Error('Invalid request: "messages" array is missing or empty.');
        }
        const lastUserMessage = messages.filter(msg => msg.role === 'user').pop();
        if (!lastUserMessage || !lastUserMessage.content) {
            throw new Error('Invalid request: No valid user message content found in the "messages" array.');
        }
        const originalPrompt = lastUserMessage.content;
        // v2.16: Pass system prompt to the template function
        const prompt = JSON_RESPONSE_PROMPT_TEMPLATE(originalPrompt, systemMessageContent);
        console.log(`构建的 Prompt (含系统提示): \"${prompt.substring(0, 200)}...\"`);

        console.log('开始页面交互...');

        // --- 选择器 ---
        const inputSelector = 'ms-prompt-input-wrapper textarea';
        const submitButtonSelector = 'button[aria-label="Run"]';
        const responseContainerSelector = 'ms-chat-turn .chat-turn-container.model';
        const responseTextSelector = 'ms-cmark-node.cmark-node'; // Target the container for raw text
        const loadingSpinnerSelector = 'button[aria-label="Run"] svg .stoppable-spinner'; // Spinner circle

        const inputField = page.locator(inputSelector);
        const submitButton = page.locator(submitButtonSelector);
        const loadingSpinner = page.locator(loadingSpinnerSelector);

        console.log(` - 等待输入框可用...`);
        try {
            await inputField.waitFor({ state: 'visible', timeout: 10000 });
        } catch (e) {
             console.error(`❌ 查找输入框失败！`);
             await saveErrorSnapshot('input_field_not_visible');
             throw new Error(`Failed to find visible input field. Error: ${e.message}`);
        }

        console.log(' - 清空并填充输入框...');
        await inputField.fill(prompt, { timeout: 15000 });

        console.log(` - 等待运行按钮可用...`);
        try {
            await expect(submitButton).toBeEnabled({ timeout: 15000 });
        } catch (e) {
            console.error(`❌ 等待运行按钮变为可用状态超时！`);
            await saveErrorSnapshot('submit_button_not_enabled_before_click');
            throw new Error(`Submit button not enabled before click. Error: ${e.message}`);
        }

        console.log(' - 点击运行按钮...');
        await submitButton.click({ timeout: 10000 }); // Increased timeout to 10s

        // --- 处理响应 ---
        console.log('处理 AI 回复...');
        const startTime = Date.now();
        let lastResponseContainer;
        let responseElement; // This still targets ms-cmark-node overall container
        let locatedResponseElements = false;

        // 定位回复元素 (still need the container)
        for (let i = 0; i < 3 && !locatedResponseElements; i++) {
             try {
                 console.log(`   尝试定位最新回复容器及文本元素 (第 ${i + 1} 次)`);
                 await page.waitForTimeout(500 + i * 500);
                 lastResponseContainer = page.locator(responseContainerSelector).last();
                 await lastResponseContainer.waitFor({ state: 'attached', timeout: 15000 });
                 // In JSON mode, we primarily care about the container (responseElement) itself
                 responseElement = lastResponseContainer.locator(responseTextSelector);
                 await responseElement.waitFor({ state: 'attached', timeout: 15000 });
                 console.log("   回复容器和文本元素定位成功。");
                 locatedResponseElements = true;
             } catch (locateError) {
                 console.warn(`   第 ${i + 1} 次定位回复元素失败: ${locateError.message.split('\n')[0]}`);
                 if (i === 2) {
                      await saveErrorSnapshot('response_locate_fail');
                      throw new Error("Failed to locate response elements after multiple attempts.");
                 }
             }
        }
        if (!locatedResponseElements) throw new Error("Could not locate response elements.");


        if (isStreaming) {
            // --- 流式处理 (v2.14 - 发送原始文本块, 可能含JSON) ---
            console.log(`  - 流式传输开始 (结束条件: Spinner消失 + 输入框空 + Run按钮禁用)...`);
            console.log(`  - 注意：将发送原始文本块，客户端需处理可能的JSON语法。`);
            let lastRawText = ""; // Store the raw text content
            let streamEnded = false;
            let spinnerIsChecking = true;

            while (!streamEnded) {
                 // 检查总超时
                if (Date.now() - startTime > RESPONSE_COMPLETION_TIMEOUT) {
                    console.warn("  - 流式处理因总超时结束。");
                    await saveErrorSnapshot('streaming_timeout');
                    streamEnded = true;
                    if (!res.writableEnded) {
                         sendStreamError(res, "Stream processing timed out on server.");
                    }
                    break;
                }

                // 1. 获取当前原始回复文本 (JSON Mode)
                const currentRawText = await getRawTextContent(responseElement, lastRawText);

                // 2. 发送文本更新 (Delta) - Sending raw delta
                if (currentRawText !== lastRawText) {
                    const delta = currentRawText.substring(lastRawText.length);
                    sendStreamChunk(res, delta);
                    lastRawText = currentRawText;
                    spinnerIsChecking = true; // Reset spinner check if text updates
                }

                // 3. 检查结束条件 (逻辑不变)
                if (spinnerIsChecking) {
                    let isSpinnerHidden = false;
                    try {
                        await expect(loadingSpinner).toBeHidden({ timeout: SPINNER_CHECK_TIMEOUT_MS });
                        isSpinnerHidden = true;
                    } catch (e) {
                        isSpinnerHidden = false;
                    }

                     if (isSpinnerHidden) {
                         console.log("   Spinner 已消失。准备检查最终页面状态...");
                         spinnerIsChecking = false;
                         await page.waitForTimeout(POST_SPINNER_CHECK_DELAY_MS);
                     }

                } else {
                    // Spinner 已经消失了，现在检查最终状态 (输入框空 + 按钮禁用)
                    console.log("   检查最终状态 (输入框空 + 按钮禁用)...");
                    let isInputEmpty = false;
                    let isButtonDisabled = false;

                     try {
                         await expect(inputField).toHaveValue('', { timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                         isInputEmpty = true;
                     } catch (e) {
                         isInputEmpty = false;
                         spinnerIsChecking = true; // Reset if input is not empty
                     }

                     if (isInputEmpty) {
                         try {
                             await expect(submitButton).toBeDisabled({ timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                             isButtonDisabled = true;
                         } catch (e) {
                             isButtonDisabled = false;
                             spinnerIsChecking = true; // Reset if button not disabled
                         }
                     }

                    // 最终判断
                    if (isInputEmpty && isButtonDisabled) {
                        console.log("   输入框为空且按钮已禁用。判定流结束。");
                        streamEnded = true;

                        // 最终原始文本捕获
                        await page.waitForTimeout(POST_COMPLETION_BUFFER);
                        const finalRawText = await getRawTextContent(responseElement, lastRawText);
                        if (finalRawText !== lastRawText) {
                            const finalDelta = finalRawText.substring(lastRawText.length);
                            sendStreamChunk(res, finalDelta);
                            lastRawText = finalRawText;
                            console.log("    (发送了在最终检查中捕获的原始 Delta)");
                        }
                        try {
                             const parsedJson = tryParseJson(lastRawText);
                             if (parsedJson && parsedJson.response) {
                                 console.log(`   (流结束) 解析最终 JSON 成功。Response 长度: ${parsedJson.response.length}`);
                             } else {
                                 console.warn("   (流结束) 警告: 未能解析最终文本为预期 JSON 结构。");
                                 console.warn(`      原始文本: \"${lastRawText.substring(0, 200)}...\"`);
                             }
                         } catch (parseError) {
                             console.error(`   (流结束) 错误: 解析最终 JSON 时出错: ${parseError.message}`);
                             console.warn(`      原始文本: \"${lastRawText.substring(0, 200)}...\"`);
                         }
                        break; // 退出 while 循环
                    }
                } // End else (checking final state)

                if (!streamEnded) {
                    await new Promise(resolve => setTimeout(resolve, POLLING_INTERVAL));
                }
            } // End while(!streamEnded)

            if (!res.writableEnded) {
                res.write('data: [DONE]\n\n');
                res.end();
                console.log('✅ 流式响应 [DONE] 已发送。');
                 console.log(`   最终原始文本长度: ${lastRawText.length}`);
            }

        } else {
             // --- 非流式处理 (v2.14 - 解析JSON, with 3s re-check logic) ---
             console.log('  - 等待 AI 处理完成 (检查 Spinner 消失 + 输入框空 + 按钮禁用)...');
             let processComplete = false;
             const nonStreamStartTime = Date.now();
             let finalStateCheckInitiated = false; // Flag to track if we are in the 3s confirmation wait

             // Completion check logic (revised with 3s re-check)
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
                          // First time detecting the state, initiate 3s wait
                          finalStateCheckInitiated = true;
                          console.log('   检测到潜在最终状态 (Spinner 消失 + 输入框空 + 按钮禁用)。等待 3 秒进行确认...');
                          await page.waitForTimeout(3000); // Wait 3 seconds

                          // Re-check the state after 3 seconds
                          console.log('   3 秒等待结束，重新检查状态...');
                          try {
                              await expect(loadingSpinner).toBeHidden({ timeout: 500 }); // Quick check
                              await expect(inputField).toHaveValue('', { timeout: 500 }); // Quick check
                              await expect(submitButton).toBeDisabled({ timeout: 500 }); // Quick check
                              // If all checks pass again, confirm completion
                              console.log('   状态确认成功。判定处理完成。');
                              processComplete = true; // Exit loop
                          } catch (recheckError) {
                              // State changed during the wait
                              console.log(`   状态在 3 秒确认期间发生变化 (${recheckError.message.split('\n')[0]})。继续轮询...`);
                              finalStateCheckInitiated = false; // Reset flag to allow re-detection
                          }
                      }
                      // If finalStateCheckInitiated is true but processComplete is still false,
                      // it means the re-check failed, so we just loop again naturally.
                  } else {
                      // Reset the check flag if the state is no longer met
                      if (finalStateCheckInitiated) {
                          console.log('   最终状态不再满足，重置确认标志。');
                          finalStateCheckInitiated = false;
                      }
                       await page.waitForTimeout(POLLING_INTERVAL * 2); // Check less frequently
                  }
              } // End while loop for completion check

              // --- Check for Page Errors BEFORE attempting to parse JSON ---
              console.log('  - 检查页面上是否存在错误提示...');
              const pageError = await detectAndExtractPageError(page);
              if (pageError) {
                  console.error(`❌ 检测到 AI Studio 页面错误: ${pageError}`);
                  await saveErrorSnapshot('page_error_detected');
                  // Throw an error to be caught by the main handler, which sends a 500 response
                  throw new Error(`AI Studio Error: ${pageError}`);
              }

              if (!processComplete) {
                   console.warn(`   警告：等待最终完成状态超时或未能稳定确认 (${(Date.now() - nonStreamStartTime) / 1000}s)。将直接尝试获取并解析JSON。`);
                    await saveErrorSnapshot('nonstream_final_state_timeout');
               } else {
                   // This runs if processComplete became true after the 3s confirmation
                   console.log('  - 开始获取并解析最终 JSON...');
               }

             // --- Get and Parse JSON (This block now runs AFTER the confirmation or the timeout warning) ---
             let aiResponseText = null;
             const maxRetries = 3;
             let attempts = 0;

             // 尝试获取原始文本并解析 JSON
             while (attempts < maxRetries && aiResponseText === null) {
                  attempts++;
                  console.log(`    - 尝试获取原始文本并解析 JSON (第 ${attempts} 次)...`);
                  try {
                      lastResponseContainer = page.locator(responseContainerSelector).last();
                      // Use 5s timeout for locating elements
                      await lastResponseContainer.waitFor({ state: 'attached', timeout: 5000 });
                      responseElement = lastResponseContainer.locator(responseTextSelector); // Still points to the main container
                      await responseElement.waitFor({ state: 'attached', timeout: 5000 });

                      // Get the raw text first
                      const rawText = await getRawTextContent(responseElement, ''); // Fetch fresh raw text

                      if (!rawText || rawText.trim() === '') {
                          console.warn(`    - 第 ${attempts} 次获取的原始文本为空。`);
                          throw new Error("Raw text content is empty.");
                      }
                       console.log(`    - 获取到原始文本 (长度: ${rawText.length}): \"${rawText.substring(0,100)}...\"`);

                      // Attempt to parse the raw text as JSON
                      const parsedJson = tryParseJson(rawText);

                      if (parsedJson && typeof parsedJson.response === 'string') {
                          aiResponseText = parsedJson.response;
                          console.log("    - 成功解析 JSON 并提取 'response' 字段。");
                          break; // Exit loop on successful parsing
                      } else {
                          console.warn(`    - 第 ${attempts} 次未能解析 JSON 或缺少 'response' 字段。`);
                          if(parsedJson) console.warn(`      Parsed structure: ${JSON.stringify(parsedJson).substring(0,100)}...`);
                          aiResponseText = null; // Ensure retry
                           if (attempts >= maxRetries) {
                              await saveErrorSnapshot('json_parse_fail_final_attempt');
                           }
                      }

                  } catch (e) {
                      console.warn(`    - 第 ${attempts} 次获取或解析失败: ${e.message.split('\n')[0]}`);
                      aiResponseText = null; // Ensure retry
                      if (attempts >= maxRetries) {
                          console.error("    - 多次尝试获取并解析 JSON 失败。");
                          await saveErrorSnapshot('get_parse_json_failed_final');
                          aiResponseText = ""; // Fallback to empty string
                      } else {
                           await new Promise(resolve => setTimeout(resolve, 1500 + attempts * 500)); // Wait longer before retry
                      }
                  }
             } // End while loop for JSON parsing

            if (aiResponseText === null) {
                 // Check again for errors specifically if JSON parsing failed completely
                 console.log('    - JSON 解析失败，再次检查页面错误...');
                 const finalCheckError = await detectAndExtractPageError(page);
                 if (finalCheckError) {
                      console.error(`❌ 检测到 AI Studio 页面错误 (在 JSON 解析失败后): ${finalCheckError}`);
                      await saveErrorSnapshot('page_error_post_json_fail');
                      throw new Error(`AI Studio Error after JSON parse failed: ${finalCheckError}`);
                 }
                  console.warn("警告：所有尝试均未能获取并解析出有效的 JSON 回复。返回空回复。");
                  aiResponseText = "";
              }

            const cleanedResponse = aiResponseText; // Already extracted from JSON
            console.log(`✅ 获取到解析后的 AI 回复 (来自JSON, 长度: ${cleanedResponse.length}): \"${cleanedResponse.substring(0, 100)}...\"`);

            const responsePayload = {
                id: `chatcmpl-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`,
                object: 'chat.completion',
                created: Math.floor(Date.now() / 1000),
                model: 'google-ai-studio-via-playwright-cdp-json', // Indicate JSON mode in model name
                choices: [{
                    index: 0,
                    message: { role: 'assistant', content: cleanedResponse },
                    finish_reason: 'stop',
                }],
                usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, // Usage data is not accurate
            };
            console.log('✅ 返回 JSON 响应。');
            res.json(responsePayload);
        }

        clearTimeout(operationTimer);

    } catch (error) {
        clearTimeout(operationTimer);
        console.error(`❌ 处理 API 请求时出错: ${error.message}\n${error.stack}`);
        if (!error.message.includes('snapshot') && !error.stack?.includes('saveErrorSnapshot')) {
             await saveErrorSnapshot(`general_api_error_${Date.now()}`);
        }

        if (!res.headersSent) {
            res.status(500).json({ error: { message: error.message, type: 'server_error' } });
        } else if (isStreaming && !res.writableEnded) {
             sendStreamError(res, error.message);
        }
        else if (!res.writableEnded) {
             res.end();
        }
    }
});

// --- Helper: 获取当前文本 (v2.14 - 获取原始文本) ---
// Renamed to clarify purpose in JSON mode
async function getRawTextContent(responseElement, previousText) {
    try {
         await responseElement.waitFor({ state: 'attached', timeout: 1500 });
         // Try to get text from a <pre> tag first, as AI studio often wraps JSON in it
         const preElement = responseElement.locator('pre').last();
         let rawText = null;
         try {
              // Use a shorter timeout for the <pre> check as it might not exist
              await preElement.waitFor({ state: 'attached', timeout: 500 });
              rawText = await preElement.textContent({ timeout: 1000 });
              // console.log("   (Debug) Got text from <pre>");
         } catch {
              // Fallback to the main container's text content if <pre> fails or times out quickly
              // console.log("   (Debug) Failed to get text from <pre>, falling back to main container.");
              rawText = await responseElement.textContent({ timeout: 2000 });
              // console.log("   (Debug) Got text from main responseElement");
         }

         // Ensure rawText is not null before trimming
         return rawText !== null ? rawText.trim() : previousText;
    } catch (e) {
         // Be less verbose on errors here as it might happen normally during streaming start
         // console.warn(`   (Warn) getRawTextContent failed: ${e.message.split('\\n')[0]}. Returning previous text.`);
         return previousText; // Return previous text on error
    }
}


// --- Helper: 发送流式块 ---
function sendStreamChunk(res, delta) {
    if (delta && !res.writableEnded) {
        const chunk = {
            id: `chatcmpl-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`,
            object: "chat.completion.chunk",
            created: Math.floor(Date.now() / 1000),
            model: "google-ai-studio-via-playwright-cdp-json", // Match model name
            choices: [{ index: 0, delta: { content: delta }, finish_reason: null }]
        };
         try {
             res.write(`data: ${JSON.stringify(chunk)}\n\n`);
         } catch (writeError) {
              console.error("Error writing stream chunk:", writeError.message);
              if (!res.writableEnded) res.end();
         }
    }
}

// --- Helper: 发送流式错误块 ---
function sendStreamError(res, errorMessage) {
     if (!res.writableEnded) {
         const errorPayload = { error: { message: `Server error during streaming: ${errorMessage}`, type: 'server_error' } };
         try {
              res.write(`data: ${JSON.stringify(errorPayload)}\n\n`);
              res.write('data: [DONE]\n\n'); // Send DONE even on error for client consistency
         } catch (e) {
             console.error("Error writing stream error chunk:", e.message);
         } finally {
             if (!res.writableEnded) res.end();
         }
     }
}

// --- Helper: 保存错误快照 ---
async function saveErrorSnapshot(errorName = 'error') {
     if (!browser?.isConnected() || !page || page.isClosed()) {
         console.log(`   无法保存错误快照 (${errorName})，浏览器或页面不可用。`);
         return;
     }
     console.log(`   尝试保存错误快照 (${errorName})...`);
     const timestamp = Date.now();
     const errorDir = path.join(__dirname, 'errors');
     try {
          if (!fs.existsSync(errorDir)) fs.mkdirSync(errorDir, { recursive: true });
          const screenshotPath = path.join(errorDir, `${errorName}_screenshot_${timestamp}.png`);
          const htmlPath = path.join(errorDir, `${errorName}_page_${timestamp}.html`);

          try {
               await page.screenshot({ path: screenshotPath, fullPage: true, timeout: 15000 });
               console.log(`   错误快照已保存到: ${screenshotPath}`);
          } catch (screenshotError) {
               console.error(`   保存屏幕截图失败 (${errorName}): ${screenshotError.message}`);
          }
          try {
               const content = await page.content({timeout: 15000});
               fs.writeFileSync(htmlPath, content);
               console.log(`   错误页面HTML已保存到: ${htmlPath}`);
          } catch (htmlError) {
                console.error(`   保存页面HTML失败 (${errorName}): ${htmlError.message}`);
          }
     } catch (dirError) {
          console.error(`   创建错误目录或保存快照时出错: ${dirError.message}`);
     }
}

// v2.14: Helper to safely parse JSON, attempting to find the outermost object/array
function tryParseJson(text) {
    if (!text || typeof text !== 'string') return null;
    text = text.trim(); // Trim leading/trailing whitespace

    // Attempt to find the first opening brace/bracket and the last closing brace/bracket
    let startIndex = -1;
    let endIndex = -1;
    let isArray = false;

    const firstBrace = text.indexOf('{');
    const firstBracket = text.indexOf('[');

    if (firstBrace !== -1 && (firstBracket === -1 || firstBrace < firstBracket)) {
        startIndex = firstBrace;
        endIndex = text.lastIndexOf('}');
    } else if (firstBracket !== -1) {
        startIndex = firstBracket;
        endIndex = text.lastIndexOf(']');
        isArray = true;
    }

    if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
        // console.warn("   (Warn) Could not find valid start/end braces/brackets for JSON parsing.");
        return null; // No valid JSON structure found
    }

    // Extract the potential JSON string
    const jsonText = text.substring(startIndex, endIndex + 1);

    try {
        return JSON.parse(jsonText);
    } catch (e) {
         // console.warn(`   (Warn) JSON parse failed for extracted text: ${e.message}`);
        return null; // Return null if parsing fails
    }
}

// --- Helper: 检测并提取页面错误提示 ---
async function detectAndExtractPageError(page) {
    const errorToastLocator = page.locator('div.toast.warning, div.toast.error').last();
    try {
        // Check if the error toast is visible with a short timeout
        const isVisible = await errorToastLocator.isVisible({ timeout: 1000 });
        if (isVisible) {
            console.log('   检测到错误 Toast 元素。');
            // Try to extract the specific message
            const messageLocator = errorToastLocator.locator('span.content-text');
            const errorMessage = await messageLocator.textContent({ timeout: 500 });
            return errorMessage || "Detected error toast, but couldn't extract specific message.";
        } else {
             // console.log('   未检测到可见的错误 Toast。');
             return null; // No visible error toast
        }
    } catch (e) {
        // Locator might timeout if element never appears, which is normal (no error)
        // console.warn(`   (Warn) Checking for error toast failed or timed out: ${e.message.split('\n')[0]}`);
        return null; // Assume no error if check fails
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