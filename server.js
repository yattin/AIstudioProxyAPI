// server.js (优化版 v2.5 - 移除输入框点击，直接 fill)

const express = require('express');
const fs = require('fs');
const path = require('path');

// --- 依赖检查 ---
let playwright;
let expect;
try {
    playwright = require('playwright');
    expect = require('@playwright/test').expect;
} catch (e) {
    console.error("❌ 错误: 依赖模块未找到。请运行:");
    console.error("   npm install express playwright @playwright/test");
    process.exit(1);
}

// --- 配置 ---
const SERVER_PORT = process.env.PORT || 3000;
const CHROME_DEBUGGING_PORT = 8848;
const CDP_ADDRESS = `http://127.0.0.1:${CHROME_DEBUGGING_PORT}`;
const AI_STUDIO_URL_PATTERN = 'aistudio.google.com/';
const RESPONSE_COMPLETION_TIMEOUT = 300000;
const POLLING_INTERVAL = 200;
const POST_COMPLETION_BUFFER = 250;

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
                if (url.includes(AI_STUDIO_URL_PATTERN)) {
                    console.log(`-> 找到 AI Studio 页面: ${url}`);
                    foundPage = p;
                    if (!url.includes('/prompts/new_chat')) {
                         console.log(`   非 new_chat 页面，尝试导航...`);
                         await foundPage.goto('https://aistudio.google.com/prompts/new_chat', { waitUntil: 'domcontentloaded', timeout: 20000 });
                         console.log(`   导航完成: ${foundPage.url()}`);
                    }
                    break;
                }
            } catch (pageError) {
                 if (!p.isClosed()) {
                     console.warn(`   警告：评估或导航页面时出错: ${pageError.message.split('\n')[0]}`);
                 }
            }
        }

        if (!foundPage) {
            throw new Error(`未在已连接的 Chrome 中找到包含 "${AI_STUDIO_URL_PATTERN}" 的页面。请确保 auto_connect_aistudio.js 已成功运行，并且 AI Studio 页面 (例如 prompts/new_chat) 已打开。`);
        }

        page = foundPage;
        console.log('-> 已定位到 AI Studio 页面。检查页面加载状态...');
        await page.bringToFront();
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 });
        console.log('-> 页面 DOM 已加载。');

        try {
            console.log("-> 尝试定位核心输入区域以确认页面就绪...");
             await page.locator('ms-prompt-input-wrapper').waitFor({ state: 'visible', timeout: 10000 });
             console.log("-> 核心输入区域容器已找到。");
        } catch(initCheckError) {
            console.warn(`⚠️ 初始化检查警告：未能快速定位到核心输入区域容器。页面可能仍在加载或结构有变: ${initCheckError.message.split('\n')[0]}`);
        }

        isPlaywrightReady = true;
        console.log('✅ Playwright 已准备就绪。');

    } catch (error) {
        console.error(`❌ 初始化 Playwright 失败: ${error.message}`);
        isPlaywrightReady = false;
        if (browser && browser.isConnected()) {
             console.log("   浏览器连接将由脚本退出时或断开事件处理。");
        }
        browser = null;
        page = null;
    } finally {
        isInitializing = false;
    }
}

// --- 中间件 ---
app.use(express.json());

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

    console.log('\n--- 收到 /v1/chat/completions 请求 ---');
    const isStreaming = req.body.stream === true;
    if (isStreaming) {
        res.setHeader('Content-Type', 'text/event-stream');
        res.setHeader('Cache-Control', 'no-cache');
        res.setHeader('Connection', 'keep-alive');
        res.setHeader('Access-Control-Allow-Origin', '*');
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
                 res.end();
            }
        }, RESPONSE_COMPLETION_TIMEOUT);


        const messages = req.body.messages;
        const lastUserMessage = messages?.filter(msg => msg.role === 'user').pop();
        if (!lastUserMessage || !lastUserMessage.content) {
            throw new Error('Invalid request: No valid user message content found in the "messages" array.');
        }
        const prompt = lastUserMessage.content;
        console.log(`提取 Prompt: "${prompt.substring(0, 100)}..."`);

        // --- Playwright 交互 ---
        console.log('开始页面交互...');

        // --- 选择器 ---
        const inputSelector = 'textarea[aria-label="Type something or pick one from prompt gallery"]';
        const submitButtonSelector = 'button[aria-label="Run"]';
        const responseContainerSelector = 'ms-chat-turn .chat-turn-container.model';
        const responseTextSelector = 'ms-cmark-node.cmark-node';
        const loadingSpinnerSelector = 'button[aria-label="Run"] svg.stoppable-spinner';

        // --- 定位元素 ---
        const inputField = page.locator(inputSelector);
        const submitButton = page.locator(submitButtonSelector);
        const loadingSpinner = page.locator(loadingSpinnerSelector);

        // --- 交互步骤 ---
        console.log(` - 等待输入框可见 (Selector: ${inputSelector})...`);
        try {
            await inputField.waitFor({ state: 'visible', timeout: 15000 });
        } catch (e) {
             console.error(`❌ 查找输入框失败！选择器可能已更改或页面状态不对。`);
             await saveErrorSnapshot('input_field_not_visible');
             throw new Error(`Failed to find visible input field using selector: ${inputSelector}. Check the latest HTML snapshot and selector validity. Original error: ${e.message}`);
        }

        console.log(' - 清空并填充输入框 (直接使用 fill)...');
        // v2.5: 移除显式的 click()，让 fill() 自动处理聚焦和可能的覆盖层
        // await inputField.click({ timeout: 5000 }); // <--- 移除此行
        await inputField.fill('', { timeout: 5000 }); // 先清空
        await inputField.fill(prompt, { timeout: 10000 }); // 再填充

        console.log(` - 等待运行按钮可用 (Selector: ${submitButtonSelector})...`);
        try {
            await expect(submitButton).toBeEnabled({ timeout: 15000 });
        } catch (e) {
            console.error(`❌ 等待运行按钮可用超时！按钮可能仍为 disabled 状态。`);
            await saveErrorSnapshot('submit_button_not_enabled');
            throw new Error(`Submit button (selector: ${submitButtonSelector}) did not become enabled within the timeout. Original error: ${e.message}`);
        }

        console.log(' - 点击运行按钮...');
        await submitButton.click({ timeout: 5000 });

        // ** 确认 AI 开始生成 **
        console.log(` - 等待加载指示器出现 (Selector: ${loadingSpinnerSelector})...`);
        try {
            await expect(loadingSpinner).toBeVisible({ timeout: 15000 });
            console.log('   加载指示器已出现，AI 开始生成...');
        } catch(visError) {
             console.warn(`   警告：未能明确检测到加载指示器出现: ${visError.message.split('\n')[0]}. 将继续等待回复...`);
        }

        // --- 处理响应 ---
        console.log('处理 AI 回复...');
        const startTime = Date.now();
        let lastResponseContainer;
        let responseElement;

        if (isStreaming) {
            // --- 流式处理 ---
            console.log('  - 流式传输开始...');
            let previousText = "";
            let lastChunkSentTime = Date.now();
            let streamEnded = false;
            let lastSuccessfulText = "";

             let retries = 0;
             const maxRetriesLocate = 3;
             while (retries < maxRetriesLocate && (!lastResponseContainer || !responseElement)) {
                try {
                     console.log(`   (流式) 尝试定位最新回复容器 (第 ${retries + 1} 次)`);
                     lastResponseContainer = page.locator(responseContainerSelector).last();
                     await lastResponseContainer.waitFor({ state: 'attached', timeout: 5000 });
                     responseElement = lastResponseContainer.locator(responseTextSelector);
                     await responseElement.waitFor({ state: 'attached', timeout: 5000 });
                     console.log("   (流式) 回复容器和文本元素定位成功。");
                     break;
                 } catch (locateError) {
                     retries++;
                     console.warn(`   (流式) 第 ${retries} 次定位回复元素失败: ${locateError.message.split('\n')[0]}`);
                     if (retries >= maxRetriesLocate) {
                          await saveErrorSnapshot('streaming_locate_fail');
                          throw new Error("Failed to locate response elements after multiple retries during streaming.");
                     }
                     await page.waitForTimeout(500);
                 }
             }

            while (!streamEnded) {
                if (Date.now() - startTime > RESPONSE_COMPLETION_TIMEOUT) {
                    console.warn("  - 流式处理因总超时结束。");
                     await saveErrorSnapshot('streaming_timeout');
                    streamEnded = true;
                    if (!res.writableEnded) res.end();
                    break;
                }

                const isSpinnerHidden = await loadingSpinner.isHidden({ timeout: 100 });

                if (isSpinnerHidden) {
                     console.log('   检测到加载指示器消失，进入缓冲和最后检查阶段...');
                     const bufferEndTime = Date.now() + POST_COMPLETION_BUFFER * 2;
                     while(Date.now() < bufferEndTime) {
                        await new Promise(resolve => setTimeout(resolve, POLLING_INTERVAL / 2));
                        const currentText = await getCurrentText(responseElement, lastSuccessfulText);
                        if (currentText !== lastSuccessfulText) {
                             const delta = currentText.substring(lastSuccessfulText.length);
                             sendStreamChunk(res, delta);
                             lastSuccessfulText = currentText;
                             lastChunkSentTime = Date.now();
                         }
                     }
                     console.log('   缓冲结束，准备发送 [DONE]。');
                     streamEnded = true;
                     break;
                }

                 const currentText = await getCurrentText(responseElement, lastSuccessfulText);

                if (currentText !== lastSuccessfulText) {
                    const delta = currentText.substring(lastSuccessfulText.length);
                     sendStreamChunk(res, delta);
                     lastSuccessfulText = currentText;
                    lastChunkSentTime = Date.now();
                }

                if (Date.now() - lastChunkSentTime > 30000 && !isSpinnerHidden) {
                    console.warn('   警告：超过30秒未收到新内容，但加载指示器仍在。可能已卡住。');
                     await saveErrorSnapshot('streaming_stalled');
                }

                await new Promise(resolve => setTimeout(resolve, POLLING_INTERVAL));
            }

            if (!res.writableEnded) {
                res.write('data: [DONE]\n\n');
                res.end();
                console.log('✅ 流式响应 [DONE] 已发送。');
            }

        } else {
            // --- 非流式处理 ---
            console.log('  - 等待加载指示器消失 (表示生成完成)...');
            try {
                 const remainingTimeout = RESPONSE_COMPLETION_TIMEOUT - (Date.now() - startTime);
                 if (remainingTimeout <= 0) throw new Error("Timeout already exceeded before waiting for spinner to hide.");
                 await expect(loadingSpinner).toBeHidden({ timeout: remainingTimeout });
                 console.log('   加载指示器已消失。');
                 await new Promise(resolve => setTimeout(resolve, POST_COMPLETION_BUFFER));
            } catch (timeoutError) {
                 console.error(`❌ 等待加载指示器消失超时或出错！`);
                 await saveErrorSnapshot('spinner_hide_timeout');
            }

            console.log('  - 获取最终完整回复文本...');
             lastResponseContainer = page.locator(responseContainerSelector).last();
             responseElement = lastResponseContainer.locator(responseTextSelector);

             let aiResponseText = null;
             const textFetchTimeout = 15000;
             const maxRetries = 3;
             let attempts = 0;

             while (attempts < maxRetries && aiResponseText === null) {
                 attempts++;
                 console.log(`    - 尝试获取最终文本 (第 ${attempts} 次)...`);
                 try {
                      await responseElement.waitFor({ state: 'attached', timeout: 5000 });
                      aiResponseText = await responseElement.textContent({ timeout: textFetchTimeout });
                      if (aiResponseText !== null && aiResponseText.trim() !== '') {
                           console.log("    - 成功获取 textContent。");
                           break;
                      } else {
                           console.warn("    - textContent 为空或仅空白，尝试 innerText...");
                           aiResponseText = await responseElement.innerText({ timeout: textFetchTimeout });
                           if (aiResponseText !== null && aiResponseText.trim() !== '') {
                              console.log("    - 成功获取 innerText。");
                              break;
                           } else {
                                console.warn("    - innerText 也为空或仅空白。");
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
                                throw new Error('Failed to retrieve final text content after multiple attempts.');
                           }
                      }
                  }
             }

            if (aiResponseText === null || aiResponseText.trim() === '') {
                await saveErrorSnapshot('empty_final_response');
                throw new Error('抓取到的最终 AI 回复文本为空或仅包含空白。');
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
            res.json(responsePayload);
        }

        clearTimeout(operationTimer);

    } catch (error) {
        clearTimeout(operationTimer);
        console.error(`❌ 处理 API 请求时出错: ${error.message}`);
        // Save snapshot, unless already saved by a specific error point
        if (!error.message.includes("Failed to find visible input field") &&
            !error.message.includes("Submit button") &&
            !error.message.includes("spinner_hide_timeout") &&
            !error.message.includes("get_final_text_failed") &&
            !error.message.includes("empty_final_response") &&
            !error.message.includes("locator.click")) { // Avoid saving again if click failed
             await saveErrorSnapshot(`general_error_${Date.now()}`);
        }

        if (!res.headersSent) {
            if (isStreaming) {
                 const errorPayload = { error: { message: error.message, type: 'server_error' } };
                 try {
                      res.write(`data: ${JSON.stringify(errorPayload)}\n\n`);
                      res.write('data: [DONE]\n\n');
                      res.end();
                 } catch(e) {
                      if (!res.writableEnded) res.end();
                 }
            } else {
                res.status(500).json({ error: { message: error.message, type: 'server_error' } });
            }
        } else if (isStreaming && !res.writableEnded) {
             res.end();
        }
    }
});

// --- Helper: 获取当前文本 (用于流式) ---
async function getCurrentText(responseElement, previousText) {
    try {
         await responseElement.waitFor({ state: 'attached', timeout: 3000 });
         const text = await responseElement.textContent({ timeout: 5000 });
         return text === null ? previousText : text; // Return previous if null
    } catch (e) {
         return previousText;
    }
}

// --- Helper: 发送流式块 ---
function sendStreamChunk(res, delta) {
    if (delta && !res.writableEnded) {
        const chunk = {
            id: `chatcmpl-${Date.now()}`,
            object: "chat.completion.chunk",
            created: Math.floor(Date.now() / 1000),
            model: "google-ai-studio-via-playwright-cdp",
            choices: [{
                index: 0,
                delta: { content: delta },
                finish_reason: null
            }]
        };
         try {
             res.write(`data: ${JSON.stringify(chunk)}\n\n`);
         } catch (writeError) {
              console.error("Error writing stream chunk:", writeError.message);
              if (!res.writableEnded) {
                   res.end();
              }
         }
    }
}


// --- Helper: 保存错误快照 ---
async function saveErrorSnapshot(errorName = 'error') {
     if (!page || page.isClosed()) {
         console.log("   无法保存错误快照，页面已关闭或不可用。");
         return;
     }
     console.log(`   尝试保存错误快照 (${errorName})...`);
     const timestamp = Date.now();
     const errorDir = path.join(__dirname, 'errors');
     try {
          if (!fs.existsSync(errorDir)) fs.mkdirSync(errorDir);
          const screenshotPath = path.join(errorDir, `${errorName}_screenshot_${timestamp}.png`);
          const htmlPath = path.join(errorDir, `${errorName}_page_${timestamp}.html`);

          await page.screenshot({ path: screenshotPath, fullPage: true, timeout: 15000 });
          fs.writeFileSync(htmlPath, await page.content({timeout: 15000}));
          console.log(`   错误快照已保存到: ${screenshotPath}`);
          console.log(`   错误页面HTML已保存到: ${htmlPath}`);
     } catch (captureError) {
          console.error(`   尝试保存错误快照失败: ${captureError.message}`);
     }
}


// --- 启动服务器 ---
let serverInstance = null;
(async () => {
    await initializePlaywright();

    serverInstance = app.listen(SERVER_PORT, () => {
        console.log(`\n🚀 OpenAI API 代理服务器(v2.5)正在监听 http://localhost:${SERVER_PORT}`);
        if (isPlaywrightReady) {
            console.log('✅ Playwright 已连接，服务器准备就绪。');
        } else {
            console.warn('⚠️ Playwright 未能成功初始化。API 请求将失败，直到连接成功。请检查 Chrome 和 auto_connect_aistudio.js 的运行状态。');
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
            if (err) {
                console.error("关闭 HTTP 服务器时出错:", err);
            } else {
                console.log("HTTP 服务器已关闭。");
            }

            if (browser && browser.isConnected()) {
                 console.log("Playwright 连接将随进程退出自动断开。");
            } else {
                 console.log("Playwright 连接不存在或已断开。");
            }

            console.log('服务器优雅关闭完成。');
            process.exit(err ? 1 : 0);
        });

        setTimeout(() => {
            console.error("优雅关闭超时，强制退出进程。");
            process.exit(1);
        }, 10000);
    } else {
        console.log("服务器实例未找到，直接退出。");
         if (browser && browser.isConnected()) {
             console.log("Playwright 连接将随进程退出自动断开。");
         }
        process.exit(0);
    }
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));