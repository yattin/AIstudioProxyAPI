// server.js (优化版 v2.12 - 流结束基于Spinner消失+输入框空+按钮禁用)

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
const POST_COMPLETION_BUFFER = 3000; // 结束检查后额外等待捕获文本

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
                     console.warn(`   警告：评估页面 URL 时出错: ${pageError.message.split('\n')[0]}`);
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
            console.warn(`⚠️ 初始化检查警告：未能快速定位到核心输入区域容器。页面可能仍在加载或结构有变: ${initCheckError.message.split('\n')[0]}`);
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
    console.log(`\n--- 收到 /v1/chat/completions 请求 (Stream: ${stream === true}) ---`);
    console.log(`  Prompt (start): "${userMessageContent?.substring(0, 80)}..."`);
    if (Object.keys(otherParams).length > 0) {
         console.log(`  Other Params: ${JSON.stringify(otherParams)}`);
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
        const prompt = lastUserMessage.content;
        console.log(`提取 Prompt: "${prompt.substring(0, 100)}..."`);

        console.log('开始页面交互...');

        // --- 选择器 ---
        const inputSelector = 'ms-prompt-input-wrapper textarea';
        const submitButtonSelector = 'button[aria-label="Run"]';
        const responseContainerSelector = 'ms-chat-turn .chat-turn-container.model';
        const responseTextSelector = 'ms-cmark-node.cmark-node';
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
        await submitButton.click({ timeout: 5000 });

        // --- 处理响应 ---
        console.log('处理 AI 回复...');
        const startTime = Date.now();
        let lastResponseContainer;
        let responseElement;
        let locatedResponseElements = false;

        // 定位回复元素
        for (let i = 0; i < 3 && !locatedResponseElements; i++) {
            try {
                console.log(`   尝试定位最新回复容器及文本元素 (第 ${i + 1} 次)`);
                await page.waitForTimeout(500 + i * 500);
                lastResponseContainer = page.locator(responseContainerSelector).last();
                await lastResponseContainer.waitFor({ state: 'attached', timeout: 7000 });
                responseElement = lastResponseContainer.locator(responseTextSelector);
                await responseElement.waitFor({ state: 'attached', timeout: 7000 });
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
            // --- 流式处理 (v2.12 - 优先检查Spinner消失，然后检查最终状态) ---
            console.log(`  - 流式传输开始 (结束条件: Spinner消失 + 输入框空 + Run按钮禁用)...`);
            let lastSuccessfulText = "";
            let streamEnded = false;
            let spinnerIsChecking = true; // Start by checking the spinner

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

                // 1. 获取当前回复文本 (即使在检查Spinner时也获取，以便发送Delta)
                const currentText = await getCurrentText(responseElement, lastSuccessfulText);

                // 2. 发送文本更新 (Delta)
                if (currentText !== lastSuccessfulText) {
                    const delta = currentText.substring(lastSuccessfulText.length);
                    sendStreamChunk(res, delta);
                    lastSuccessfulText = currentText;
                    // If text updates, we reset the need to check for spinner disappearance immediately
                    spinnerIsChecking = true;
                }

                // 3. 检查结束条件 (核心逻辑)
                if (spinnerIsChecking) {
                    // 检查 Spinner 是否消失
                    let isSpinnerHidden = false;
                    try {
                        await expect(loadingSpinner).toBeHidden({ timeout: SPINNER_CHECK_TIMEOUT_MS });
                        isSpinnerHidden = true;
                    } catch (e) {
                        // Spinner 仍然可见或检查失败
                        console.log(`   (等待 Spinner (${loadingSpinnerSelector}) 消失...)`);
                        isSpinnerHidden = false;
                    }

                    if (isSpinnerHidden) {
                        // Spinner 刚刚消失！
                        console.log("   Spinner 已消失。准备检查最终页面状态...");
                        spinnerIsChecking = false; // 不再需要检查 Spinner
                        // 短暂等待，让页面状态稳定下来
                        await page.waitForTimeout(POST_SPINNER_CHECK_DELAY_MS);
                    }
                    // 如果 Spinner 还在，继续下一次轮询
                } else {
                    // Spinner 已经消失了，现在检查最终状态 (输入框空 + 按钮禁用)
                    console.log("   检查最终状态 (输入框空 + 按钮禁用)...");
                    let isInputEmpty = false;
                    let isButtonDisabled = false;

                    try {
                        // Check if input field is empty
                        await expect(inputField).toHaveValue('', { timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                        isInputEmpty = true;
                    } catch (e) {
                        console.log(`    - 输入框不为空或检查超时 (${e.message.split('\n')[0]})`);
                        isInputEmpty = false;
                         // 如果输入框不为空，很可能还在生成或用户输入了，重置Spinner检查
                         spinnerIsChecking = true;
                    }

                    // Only check button if input is empty
                    if (isInputEmpty) {
                        try {
                            // Check if submit button is disabled
                            await expect(submitButton).toBeDisabled({ timeout: FINAL_STATE_CHECK_TIMEOUT_MS });
                            isButtonDisabled = true;
                        } catch (e) {
                            console.log(`    - 按钮未禁用或检查超时 (${e.message.split('\n')[0]})`);
                            isButtonDisabled = false;
                             // 如果输入框空但按钮未禁用，可能还在最后处理阶段，重置Spinner检查
                             spinnerIsChecking = true;
                        }
                    }

                    // 最终判断
                    if (isInputEmpty && isButtonDisabled) {
                        // **所有条件满足**
                        console.log("   输入框为空且按钮已禁用。判定流结束。");
                        streamEnded = true;

                        // 最终文本捕获
                        await page.waitForTimeout(POST_COMPLETION_BUFFER);
                        const finalText = await getCurrentText(responseElement, lastSuccessfulText);
                        if (finalText !== lastSuccessfulText) {
                            const finalDelta = finalText.substring(lastSuccessfulText.length);
                            sendStreamChunk(res, finalDelta);
                            lastSuccessfulText = finalText;
                            console.log("    (发送了在最终检查中捕获的 Delta)");
                        }
                        break; // 退出 while 循环
                    }
                    // 如果条件不满足（输入框非空或按钮未禁用），循环将继续，
                    // 并且 spinnerIsChecking 可能已被重置为 true，会重新检查 spinner
                } // End else (checking final state)

                if (!streamEnded) {
                    await new Promise(resolve => setTimeout(resolve, POLLING_INTERVAL));
                }
            } // End while(!streamEnded)

            // 确保发送 [DONE] 信号
            if (!res.writableEnded) {
                res.write('data: [DONE]\n\n');
                res.end();
                console.log('✅ 流式响应 [DONE] 已发送。');
                console.log(`   累积文本 (长度: ${lastSuccessfulText.length}): "${lastSuccessfulText.substring(0, 200)}..."`);
            }

        } else {
            // --- 非流式处理 ---
             console.log('  - 等待 AI 处理完成 (检查 Spinner 消失 + 输入框空 + 按钮禁用)...');
             let processComplete = false;
             const nonStreamStartTime = Date.now();

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
                     processComplete = true;
                     console.log('   检测到最终状态 (Spinner 消失 + 输入框空 + 按钮禁用)。');
                 } else {
                     // Wait before checking again
                     await page.waitForTimeout(POLLING_INTERVAL * 2); // Check less frequently in non-streaming
                 }
             }

             if (!processComplete) {
                  console.warn(`   警告：等待最终完成状态超时 (${RESPONSE_COMPLETION_TIMEOUT / 1000}s)。继续尝试获取文本。`);
                  await saveErrorSnapshot('nonstream_final_state_timeout');
             }

            console.log(`  - 缓冲 ${POST_COMPLETION_BUFFER}ms 后获取最终文本...`);
            await new Promise(resolve => setTimeout(resolve, POST_COMPLETION_BUFFER));

            let aiResponseText = null;
            const textFetchTimeout = 15000;
            const maxRetries = 3;
            let attempts = 0;

            // 尝试获取最终文本
            while (attempts < maxRetries && aiResponseText === null) {
                 attempts++;
                 console.log(`    - 尝试获取最终文本 (第 ${attempts} 次)...`);
                 try {
                     lastResponseContainer = page.locator(responseContainerSelector).last();
                     await lastResponseContainer.waitFor({ state: 'attached', timeout: 5000 });
                     responseElement = lastResponseContainer.locator(responseTextSelector);
                     await responseElement.waitFor({ state: 'attached', timeout: 5000 });

                     aiResponseText = await responseElement.innerText({ timeout: textFetchTimeout });
                     if (aiResponseText !== null && aiResponseText.trim() !== '') {
                        console.log("    - 成功获取 innerText。");
                        break;
                     } else {
                        console.warn("    - innerText 为空或仅空白，尝试 textContent...");
                        aiResponseText = await responseElement.textContent({ timeout: textFetchTimeout });
                         if (aiResponseText !== null && aiResponseText.trim() !== '') {
                              console.log("    - 成功获取 textContent。");
                              break;
                         } else {
                              console.warn("    - textContent 也为空或仅空白。");
                              aiResponseText = null;
                         }
                     }
                 } catch (e) {
                     console.warn(`    - 第 ${attempts} 次获取文本失败: ${e.message.split('\n')[0]}`);
                     if (attempts < maxRetries) {
                          await new Promise(resolve => setTimeout(resolve, 1000));
                     } else {
                          console.warn("    - 常规方法获取文本失败，尝试获取整个回复容器的 textContent...");
                          try {
                              await lastResponseContainer.waitFor({ state: 'attached', timeout: 5000 });
                              aiResponseText = await lastResponseContainer.textContent({ timeout: 8000 });
                           } catch (eContainer) {
                               console.error(`    - 获取整个容器 textContent 也失败: ${eContainer.message.split('\n')[0]}`);
                               await saveErrorSnapshot('get_final_text_failed');
                               aiResponseText = "";
                           }
                     }
                 }
            }

            if (aiResponseText === null) {
                 await saveErrorSnapshot('empty_final_response_after_fail');
                 console.warn("警告：未能抓取到 AI 回复文本，但处理流程已完成。返回空回复。")
                 aiResponseText = "";
            }

            const cleanedResponse = aiResponseText.trim();
            console.log(`✅ 获取到完整 AI 回复 (长度: ${cleanedResponse.length}): "${cleanedResponse.substring(0, 100)}..."`);

            const responsePayload = {
                id: `chatcmpl-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`,
                object: 'chat.completion',
                created: Math.floor(Date.now() / 1000),
                model: 'google-ai-studio-via-playwright-cdp',
                choices: [{
                    index: 0,
                    message: { role: 'assistant', content: cleanedResponse },
                    finish_reason: 'stop',
                }],
                usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
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

// --- Helper: 获取当前文本 ---
async function getCurrentText(responseElement, previousText) {
    try {
         await responseElement.waitFor({ state: 'attached', timeout: 1500 });
         const text = await responseElement.innerText({ timeout: 2500 });
         return text === null ? previousText : text;
    } catch (e) {
         return previousText;
    }
}

// --- Helper: 发送流式块 ---
function sendStreamChunk(res, delta) {
    if (delta && !res.writableEnded) {
        const chunk = {
            id: `chatcmpl-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`,
            object: "chat.completion.chunk",
            created: Math.floor(Date.now() / 1000),
            model: "google-ai-studio-via-playwright-cdp",
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
              res.write('data: [DONE]\n\n');
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

// --- 启动服务器 ---
let serverInstance = null;
(async () => {
    await initializePlaywright();

    serverInstance = app.listen(SERVER_PORT, () => {
        console.log(`\n🚀 OpenAI API 代理服务器(v2.12)正在监听 http://localhost:${SERVER_PORT}`);
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