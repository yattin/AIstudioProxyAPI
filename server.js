// server.js (完整最新版 - 使用 expect().toBeEnabled())

const express = require('express');
const fs = require('fs');
const path = require('path');
const { expect } = require('@playwright/test'); // 引入 expect

// --- 配置 ---
const SERVER_PORT = process.env.PORT || 3000;
const CHROME_DEBUGGING_PORT = 8848;
const CDP_ADDRESS = `http://127.0.0.1:${CHROME_DEBUGGING_PORT}`;
const TARGET_URL = 'https://aistudio.google.com/prompts/new_chat';
const AI_STUDIO_URL_PATTERN = 'aistudio.google.com/';

let playwright;
try {
    playwright = require('playwright');
} catch (e) {
    console.error("❌ 错误: Playwright 模块未找到。请先运行 'npm install playwright @playwright/test'");
    process.exit(1);
}

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

        browser.on('disconnected', () => {
            console.error('❌ Playwright 与 Chrome 的连接已断开！需要重新启动服务器或 Chrome。');
            isPlaywrightReady = false;
            browser = null;
            page = null;
        });

        const context = browser.contexts()[0];
        if (!context) {
            throw new Error('无法获取浏览器上下文。');
        }

        let foundPage = null;
        const pages = context.pages();
        console.log(`-> 发现 ${pages.length} 个页面。正在搜索 AI Studio (匹配 "${AI_STUDIO_URL_PATTERN}")...`);
        for (const p of pages) {
            try {
                const url = p.url();
                console.log(`   检查页面: ${url}`);
                if (url.includes(AI_STUDIO_URL_PATTERN)) {
                    console.log(`-> 找到 AI Studio 页面: ${url}`);
                    foundPage = p;
                    break;
                }
            } catch (pageError) {
                 console.warn(`   警告：评估页面 URL 时出错: ${pageError.message.split('\n')[0]}`);
            }
        }

        if (!foundPage) {
            throw new Error(`未在已连接的 Chrome 中找到包含 "${AI_STUDIO_URL_PATTERN}" 的页面。请先运行 auto_connect_aistudio.js 并确保 AI Studio 页面已打开。`);
        }

        page = foundPage;
        console.log('-> 已定位到 AI Studio 页面。检查页面是否加载完成...');
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 });

        isPlaywrightReady = true;
        console.log('✅ Playwright 已准备就绪。');

    } catch (error) {
        console.error(`❌ 初始化 Playwright 失败: ${error.message}`);
        console.error('   请确保 Chrome 正确运行 (通过 auto_connect_aistudio.js 启动)，并监听调试端口，且 AI Studio 页面已打开。');
        isPlaywrightReady = false;
        if (browser && browser.isConnected()) {
             await browser.disconnect().catch(e => console.error("断开连接时出错:", e));
        }
        browser = null;
        page = null;
    } finally {
        isInitializing = false;
    }
}

// --- 中间件 ---
app.use(express.json());

// --- 健康检查端点 ---
app.get('/health', (req, res) => {
    if (isPlaywrightReady && page && !page.isClosed() && browser?.isConnected()) {
        res.status(200).json({ status: 'OK', message: 'Server is running and Playwright is connected.' });
    } else {
        const reasons = [];
        if (!isPlaywrightReady) reasons.push("Playwright not ready");
        if (!page || page?.isClosed()) reasons.push("Target page not available or closed");
        if (!browser?.isConnected()) reasons.push("Browser disconnected");
        res.status(503).json({ status: 'Error', message: `Service Unavailable. Issues: ${reasons.join(', ')}.` });
    }
});

// --- OpenAI 兼容的 Chat API 端点 ---
app.post('/v1/chat/completions', async (req, res) => {
    if (!isPlaywrightReady && !isInitializing) {
        console.warn('Playwright 未就绪，尝试重新初始化...');
        await initializePlaywright();
    }

    if (!isPlaywrightReady || !page || page.isClosed() || !browser?.isConnected()) {
        console.error('API 请求失败：Playwright 仍未就绪、页面关闭或连接断开。');
        return res.status(503).json({
            error: { message: 'Playwright connection is not active. Please ensure Chrome is running correctly and restart the server or run auto_connect_aistudio.js.', type: 'server_error' }
        });
    }

    console.log('\n--- 收到 /v1/chat/completions 请求 ---');
    // console.log('请求体:', JSON.stringify(req.body, null, 2));

    // ** 添加流式处理逻辑判断 **
    const isStreaming = req.body.stream === true;
    if (isStreaming) {
        // 设置 SSE 响应头
        res.setHeader('Content-Type', 'text/event-stream');
        res.setHeader('Cache-Control', 'no-cache');
        res.setHeader('Connection', 'keep-alive');
        // 可能需要设置 CORS 头，如果你的客户端和服务器不在同一源
        res.setHeader('Access-Control-Allow-Origin', '*'); // 谨慎使用 '*'
        res.flushHeaders(); // 发送头信息
        console.log("请求为流式请求 (stream=true)，将使用 SSE 返回响应。");
    } else {
         console.log("请求为非流式请求 (stream=false or missing)。");
    }

    try {
        const messages = req.body.messages;
        const lastUserMessage = messages?.filter(msg => msg.role === 'user').pop();
        if (!lastUserMessage || !lastUserMessage.content) {
             // 对于流式请求，也需要返回错误，但格式稍有不同
             if (isStreaming) {
                  res.write(`data: ${JSON.stringify({error: { message: 'Invalid request: No user message content found.', type: 'invalid_request_error' }})}\n\n`);
                  res.end();
             } else {
                  res.status(400).json({ error: { message: 'Invalid request: No user message content found.', type: 'invalid_request_error' } });
             }
             return;
        }
        const prompt = lastUserMessage.content;
        console.log(`提取到的 Prompt: "${prompt.substring(0,100)}..."`);

        // --- Playwright 交互 ---
        console.log('开始与 AI Studio 页面交互...');

        const inputSelector = 'ms-autosize-textarea textarea[aria-label="Type something"]';
        const submitButtonSelector = 'button[aria-label="Run"]';
        const responseContainerSelector = 'ms-chat-turn .chat-turn-container.model';
        const responseTextSelector = 'ms-cmark-node.cmark-node';

        console.log(` - 定位输入框: ${inputSelector}`);
        const inputField = page.locator(inputSelector);
        console.log(` - 定位发送按钮: ${submitButtonSelector}`);
        const submitButton = page.locator(submitButtonSelector);

        console.log(' - 等待输入框可见并填充...');
        await inputField.waitFor({ state: 'visible', timeout: 10000 });
        await inputField.click({ timeout: 5000 });
        await inputField.fill(prompt, { timeout: 10000 });

        // ** 使用 expect 等待按钮可用 **
        console.log(' - 等待发送按钮可用 (toBeEnabled)...');
        await expect(submitButton).toBeEnabled({ timeout: 15000 }); // 等待按钮变为可用

        console.log(' - 发送按钮已可用，点击...');
        await submitButton.click({ timeout: 5000 });
        console.log(' - Prompt 已发送！');

        // --- 等待并抓取/流式传输回复 ---
        console.log('等待 AI 回复...');
        try {
            console.log(`  - 等待新的回复容器 (${responseContainerSelector}) 出现...`);
            const initialResponseCount = await page.locator(responseContainerSelector).count();
            console.log(`   (初始模型回复容器数量: ${initialResponseCount})`);

            // ** 重要: 这个 waitForFunction 现在是等待回复开始出现的信号 **
            await page.waitForFunction(
                (selector, initialCount) => {
                    const elements = document.querySelectorAll(selector);
                    return elements.length > initialCount && elements[elements.length - 1].offsetParent !== null;
                },
                { selector: responseContainerSelector, initialCount: initialResponseCount },
                { timeout: 180000 }
            );
            const finalResponseCount = await page.locator(responseContainerSelector).count();
            console.log(`  - 新的回复容器已出现 (当前数量: ${finalResponseCount})。`);

            const lastResponseContainer = page.locator(responseContainerSelector).last();
            const responseElement = lastResponseContainer.locator(responseTextSelector);
            await responseElement.waitFor({ state: 'visible', timeout: 20000 });

            // --- 处理响应：流式 vs 非流式 ---
            if (isStreaming) {
                // --- 流式处理 ---
                console.log('  - 开始流式传输回复...');
                let previousText = "";
                let streamingFinished = false;
                const streamInterval = 100; // 每 100ms 检查一次更新
                const streamTimeout = 180000; // 流式传输总超时 (3分钟)
                const startTime = Date.now();

                while (Date.now() - startTime < streamTimeout && !streamingFinished) {
                    let currentText = "";
                    try {
                        // 尝试获取当前文本
                        currentText = await responseElement.textContent({ timeout: 5000 }) || ""; // 短超时获取，失败则为空
                    } catch (e) {
                        // 获取文本失败可能是元素暂时消失或变化，忽略本次轮询
                         console.warn(`    (流式) 获取 textContent 时出现临时错误: ${e.message.split('\n')[0]}`);
                         currentText = previousText; // 保持上次的文本
                    }

                    if (currentText !== previousText) {
                        const delta = currentText.substring(previousText.length);
                        if (delta) {
                            const chunk = {
                                id: `chatcmpl-${Date.now()}`, // 可以简化
                                object: "chat.completion.chunk",
                                created: Math.floor(Date.now() / 1000),
                                model: "google-ai-studio-via-playwright-cdp",
                                choices: [{ index: 0, delta: { content: delta }, finish_reason: null }]
                            };
                             // 发送 SSE 数据块
                            res.write(`data: ${JSON.stringify(chunk)}\n\n`);
                            // console.log(`    Sent chunk: ${delta.substring(0, 30)}...`); // 调试日志
                        }
                        previousText = currentText;
                    }

                    // ** 检查停止条件 (需要根据 AI Studio 页面调整!) **
                    //  - 方式1: 查找 "停止生成" 按钮是否消失或禁用？
                    //  - 方式2: 查找是否有特定的 class 或属性表示生成完成？
                    //  - 方式3: 如果文本在一段时间内没有变化，认为结束 (如下简单实现)
                    //  简单的超时/无变化检测（需要更可靠的停止信号）
                    //  if (Date.now() - lastUpdateTime > NO_CHANGE_TIMEOUT) {
                    //       console.log("  - 检测到文本在一段时间内无变化，假定流结束。");
                    //       streamingFinished = true;
                    //       break;
                    //  }
                    //  ** 暂时我们依赖外部超时或 [DONE] 信号 **
                    //  TODO: 需要找到一个可靠的方式判断 AI Studio 是否已停止生成

                    await new Promise(resolve => setTimeout(resolve, streamInterval)); // 等待一小段时间再检查
                }

                if (!streamingFinished) {
                    console.warn("  - 流式传输可能因超时而结束。");
                }

                // 发送最后的 [DONE] 信号
                res.write('data: [DONE]\n\n');
                res.end(); // 结束响应流
                console.log('✅ 流式响应发送完毕。');

            } else {
                // --- 非流式处理 (一次性获取完整文本) ---
                console.log('  - 开始获取完整回复文本...');
                let aiResponseText = null;
                 const textFetchTimeout = 15000;
                 try {
                     console.log('    - 尝试获取 textContent...');
                     aiResponseText = await responseElement.textContent({ timeout: textFetchTimeout });
                 } catch (e) {
                     console.warn(`    - 获取 textContent 失败或超时: ${e.message.split('\n')[0]}`);
                     try {
                         console.log('    - 尝试获取 innerText...');
                         aiResponseText = await responseElement.innerText({ timeout: textFetchTimeout });
                     } catch (e2) {
                         console.warn(`    - 获取 innerText 也失败: ${e2.message.split('\n')[0]}`);
                         try {
                             console.log('    - 尝试获取整个容器的 textContent...');
                             aiResponseText = await lastResponseContainer.textContent({ timeout: 8000 });
                         } catch(e3) {
                              console.error(`    - 获取整个容器 textContent 也失败: ${e3.message.split('\n')[0]}`);
                               const containerHTML = await lastResponseContainer.innerHTML().catch(() => '无法获取容器 HTML');
                               console.error('无法通过任何方法获取回复文本。容器 HTML:', containerHTML);
                               throw new Error('Failed to retrieve text content using textContent, innerText, or container textContent.');
                         }
                     }
                 }

                if (aiResponseText === null || aiResponseText.trim() === '') {
                    const containerHTML = await lastResponseContainer.innerHTML().catch(() => '无法获取容器 HTML');
                    console.error('抓取到的 AI 回复文本为空或仅包含空白。容器 HTML:', containerHTML);
                    throw new Error('抓取到的 AI 回复文本为空或仅包含空白。');
                }
                const cleanedResponse = aiResponseText.trim();
                console.log(`✅ 获取到完整 AI 回复: "${cleanedResponse.substring(0, 100)}..."`);

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
                res.json(responsePayload); // 返回完整 JSON 响应
            }

        } catch (waitError) {
            console.error(`❌ 等待或处理 AI 回复时出错: ${waitError.message}`);
            const timestamp = Date.now();
            const errorDir = path.join(__dirname, 'errors');
            try {
                 if (!fs.existsSync(errorDir)) fs.mkdirSync(errorDir);
                 const screenshotPath = path.join(errorDir, `error_screenshot_${timestamp}.png`);
                 const htmlPath = path.join(errorDir, `error_page_${timestamp}.html`);
                 await page.screenshot({ path: screenshotPath, fullPage: true });
                 fs.writeFileSync(htmlPath, await page.content());
                 console.log(`   错误快照已保存到: ${screenshotPath}`);
                 console.log(`   错误页面HTML已保存到: ${htmlPath}`);
            } catch (captureError) {
                console.error(`   尝试保存错误快照失败: ${captureError.message}`);
            }
            // 对于流式和非流式都需要返回错误
            if (!res.headersSent) { // 检查头是否已发送，防止重复发送
                 if (isStreaming) {
                     // 对于流式错误，可以尝试发送一个错误事件，但不保证客户端能收到
                     try {
                         res.write(`data: ${JSON.stringify({error: {message: `Failed during AI response processing: ${waitError.message}`, type: 'server_error'}})}\n\n`);
                     } catch (writeError) {
                          console.error("向流写入错误信息失败:", writeError);
                     } finally {
                          res.end(); // 必须结束流
                     }
                 } else {
                      res.status(500).json({ error: { message: `Failed during AI response processing: ${waitError.message}`, type: 'server_error' } });
                 }
             } else if(isStreaming) {
                  // 如果头已发送 (流式)，只能尝试结束流
                  res.end();
             }
             // 不需要再向上抛出错误，因为响应已经处理
             // throw new Error(`Failed during AI response processing: ${waitError.message}`);
        }

    } catch (error) {
        console.error(`❌ 处理 /v1/chat/completions 请求时发生顶层错误: ${error.message}`);
        if (page?.isClosed() || !browser?.isConnected()) {
            isPlaywrightReady = false;
            console.error('   检测到页面已关闭或浏览器连接已断开。');
        }
        // 确保在顶层错误时也能正确返回错误
        if (!res.headersSent) {
            if (isStreaming) {
                try {
                    res.setHeader('Content-Type', 'application/json'); // 改回 JSON 错误
                    res.status(500).json({ error: { message: error.message || 'An unexpected server error occurred.', type: 'server_error' } });
                } catch (e) { // 如果设置头失败（理论上不应发生），尝试结束流
                     res.end();
                }
            } else {
                res.status(500).json({ error: { message: error.message || 'An unexpected server error occurred.', type: 'server_error' } });
            }
        } else if(isStreaming && !res.writableEnded) {
             // 如果是流式且头已发，尝试结束
             res.end();
        }
    }
});

// --- 启动服务器 ---
(async () => {
    await initializePlaywright(); // 启动时初始化

    app.listen(SERVER_PORT, () => {
        console.log(`\n🚀 OpenAI API 代理服务器正在监听 http://localhost:${SERVER_PORT}`);
        if (isPlaywrightReady) {
            console.log('✅ Playwright 已连接，服务器准备就绪。');
        } else {
            console.warn('⚠️ Playwright 未能成功初始化。');
        }
        console.log('确保 Chrome (由 auto_connect_aistudio.js 启动) 正在运行...');
    });
})();

// --- 优雅关闭处理 ---
async function shutdown() {
    console.log('\n正在关闭服务器和 Playwright 连接...');
     if (browser && browser.isConnected()) {
        try {
            await browser.disconnect();
            console.log('Playwright 客户端连接已断开。');
        } catch (e) {
            console.error('断开 Playwright 连接时出错:', e);
        }
    }
    console.log('服务器关闭。');
    process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);