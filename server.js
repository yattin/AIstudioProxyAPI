// server.js (优化版 v2.3 - 调整输入框选择器)

const express = require('express');
const fs = require('fs');
const path = require('path');

// --- 依赖检查 ---
let playwright;
let expect;
try {
    playwright = require('playwright');
    // expect 需要从 @playwright/test 引入
    expect = require('@playwright/test').expect;
} catch (e) {
    console.error("❌ 错误: 依赖模块未找到。请运行:");
    console.error("   npm install express playwright @playwright/test");
    process.exit(1);
}


// --- 配置 ---
const SERVER_PORT = process.env.PORT || 3000;
const CHROME_DEBUGGING_PORT = 8848; // 应与 auto_connect_aistudio.js 保持一致
const CDP_ADDRESS = `http://127.0.0.1:${CHROME_DEBUGGING_PORT}`;
const AI_STUDIO_URL_PATTERN = 'aistudio.google.com/';
const RESPONSE_COMPLETION_TIMEOUT = 300000; // AI 回复的总超时时间 (5分钟)
const POLLING_INTERVAL = 200; // 流式处理时检查更新的间隔 (毫秒)
const POST_COMPLETION_BUFFER = 250; // 检测到加载结束后额外等待的时间 (毫秒) - 稍微增加

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

        // 稍微等待，确保上下文和页面信息同步
        await new Promise(resolve => setTimeout(resolve, 500));

        const contexts = browser.contexts();
        if (!contexts || contexts.length === 0) {
            // 尝试再次获取，有时连接后需要一点时间
             await new Promise(resolve => setTimeout(resolve, 1500));
             contexts = browser.contexts();
             if (!contexts || contexts.length === 0) {
                 throw new Error('无法获取浏览器上下文。请检查 Chrome 是否已正确启动并响应。');
             }
        }
        const context = contexts[0];


        let foundPage = null;
        const pages = context.pages();
        console.log(`-> 发现 ${pages.length} 个页面。正在搜索 AI Studio (匹配 "${AI_STUDIO_URL_PATTERN}")...`);
        for (const p of pages) {
            try {
                 if (p.isClosed()) {
                     console.log("   跳过一个已关闭的页面。");
                     continue;
                 }
                const url = p.url();
                // console.log(`   检查页面: ${url}`); // 调试时取消注释
                if (url.includes(AI_STUDIO_URL_PATTERN)) {
                    console.log(`-> 找到 AI Studio 页面: ${url}`);
                    foundPage = p;
                    break;
                }
            } catch (pageError) {
                 if (!p.isClosed()) { // 避免页面已关闭导致的访问错误
                     console.warn(`   警告：评估页面 URL 时出错: ${pageError.message.split('\n')[0]}`);
                 }
            }
        }

        if (!foundPage) {
            throw new Error(`未在已连接的 Chrome 中找到包含 "${AI_STUDIO_URL_PATTERN}" 的页面。请确保 auto_connect_aistudio.js 已成功运行，并且 AI Studio 页面 (例如 prompts/new_chat) 已打开。`);
        }

        page = foundPage;
        console.log('-> 已定位到 AI Studio 页面。检查页面加载状态...');
        await page.bringToFront(); // 尝试将页面置于前台
        await page.waitForLoadState('domcontentloaded', { timeout: 15000 }); // 确保DOM加载
        console.log('-> 页面 DOM 已加载。');


        // **增加一个对核心输入区域存在的检查作为初始化确认**
        try {
            console.log("-> 尝试定位核心输入区域以确认页面就绪...");
            // 使用稍微宽松的选择器检查外层容器是否存在
             await page.locator('ms-prompt-input-wrapper').waitFor({ state: 'visible', timeout: 10000 });
             console.log("-> 核心输入区域容器已找到。");
        } catch(initCheckError) {
            console.warn(`⚠️ 初始化检查警告：未能快速定位到核心输入区域容器。页面可能仍在加载或结构有变: ${initCheckError.message.split('\n')[0]}`);
            // 不在此处中断，让后续请求处理时再具体检查输入框
        }


        isPlaywrightReady = true;
        console.log('✅ Playwright 已准备就绪。');

    } catch (error) {
        console.error(`❌ 初始化 Playwright 失败: ${error.message}`);
        isPlaywrightReady = false;
        if (browser && browser.isConnected()) {
             // connectOverCDP 返回的 Browser 对象没有 disconnect 方法，依赖连接自然断开
             // await browser.disconnect().catch(e => console.error("初始化失败后断开连接时出错:", e));
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
        // 如果正在初始化，也告知用户
        if (isInitializing) reasons.push("Playwright is currently initializing");
        res.status(503).json({ status: 'Error', message: `Service Unavailable. Issues: ${reasons.join(', ')}.` });
    }
});

// --- API 端点 ---
app.post('/v1/chat/completions', async (req, res) => {
    if (!isPlaywrightReady && !isInitializing) {
        console.warn('Playwright 未就绪，尝试重新初始化...');
        await initializePlaywright(); // 尝试再次初始化
    }

    if (!isPlaywrightReady || !page || page.isClosed() || !browser?.isConnected()) {
        console.error('API 请求失败：Playwright 未就绪、页面关闭或连接断开。');
         // 尝试提供更详细的诊断信息
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
        res.setHeader('Access-Control-Allow-Origin', '*'); // 允许跨域，生产环境请指定具体来源
        res.flushHeaders();
        console.log("模式: 流式 (SSE)");
    } else {
        console.log("模式: 非流式 (JSON)");
    }

    let operationTimer; // 用于整体操作超时

    try {
        // 设置整体操作超时计时器
        operationTimer = setTimeout(() => {
            // 超时也尝试记录快照
             saveErrorSnapshot('operation_timeout');
            throw new Error(`Operation timed out after ${RESPONSE_COMPLETION_TIMEOUT / 1000} seconds.`);
        }, RESPONSE_COMPLETION_TIMEOUT);


        const messages = req.body.messages;
        const lastUserMessage = messages?.filter(msg => msg.role === 'user').pop();
        if (!lastUserMessage || !lastUserMessage.content) {
            // 改进错误消息
            throw new Error('Invalid request: No valid user message content found in the "messages" array.');
        }
        const prompt = lastUserMessage.content;
        console.log(`提取 Prompt: "${prompt.substring(0, 100)}..."`);

        // --- Playwright 交互 ---
        console.log('开始页面交互...');

        // --- 更新和确认的选择器 ---
        // v2.3: 直接定位 textarea，移除外层 ms-autosize-textarea 依赖
        const inputSelector = 'textarea[aria-label="Type something"]';
        // 保持不变，基于 HTML 分析是准确的
        const submitButtonSelector = 'button[aria-label="Run"]';
        // 模型回复的外层容器 (保持不变, 需测试验证)
        const responseContainerSelector = 'ms-chat-turn .chat-turn-container.model';
        // 回复文本的具体节点 (保持不变, 需测试验证)
        const responseTextSelector = 'ms-cmark-node.cmark-node';
        // 加载指示器选择器 (保持不变, 它只在加载时出现)
        const loadingSpinnerSelector = 'button[aria-label="Run"] svg.stoppable-spinner';


        // --- 定位元素 ---
        const inputField = page.locator(inputSelector);
        const submitButton = page.locator(submitButtonSelector);
        const loadingSpinner = page.locator(loadingSpinnerSelector); // 定位加载指示器

        // --- 交互步骤 ---
        console.log(` - 等待输入框可见 (Selector: ${inputSelector})...`);
        try {
            // 增加一点页面稳定时间
             await page.waitForTimeout(500);
            await inputField.waitFor({ state: 'visible', timeout: 15000 }); // 稍微增加超时
        } catch (e) {
             console.error(`❌ 查找输入框失败！页面可能未完全加载、结构已更改，或被遮挡。`);
             await saveErrorSnapshot('input_field_not_visible'); // 保存快照帮助诊断
             throw new Error(`Failed to find visible input field using selector: ${inputSelector}. Check page state and selector validity. Original error: ${e.message}`);
        }

        console.log(' - 清空并填充输入框...');
        await inputField.click({ timeout: 5000 }); // 点击以确保焦点
        await inputField.fill('', { timeout: 5000 }); // 先清空
        await inputField.fill(prompt, { timeout: 10000 });

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
            // 等待 spinner 出现的时间可以稍微长一点，网络延迟可能导致按钮点击后稍有停顿
            await expect(loadingSpinner).toBeVisible({ timeout: 15000 });
            console.log('   加载指示器已出现，AI 开始生成...');
        } catch(visError) {
             console.warn(`   警告：未能明确检测到加载指示器出现: ${visError.message.split('\n')[0]}. 可能是指示器选择器已更改或出现太快。将继续等待回复...`);
             // 如果 spinner 未按预期出现，仍然继续尝试等待回复，增加容错性
        }


        // --- 处理响应 ---
        console.log('处理 AI 回复...');
        const startTime = Date.now();
        let lastResponseContainer; // 移到循环外，避免重复查找
        let responseElement;     // 移到循环外

        if (isStreaming) {
            // --- 流式处理 ---
            console.log('  - 流式传输开始...');
            let previousText = "";
            let lastChunkSentTime = Date.now(); // 记录上次发送数据块的时间
            let streamEnded = false;

            // 在循环开始前，先定位到预期的最新回复容器
            // 注意：AI Studio 可能会创建新的 turn 容器，所以这里的 .last() 很重要
            lastResponseContainer = page.locator(responseContainerSelector).last();
            responseElement = lastResponseContainer.locator(responseTextSelector);


            while (!streamEnded) {
                // 检查整体操作是否超时
                if (Date.now() - startTime > RESPONSE_COMPLETION_TIMEOUT) {
                    console.warn("  - 流式处理因总超时结束。");
                     await saveErrorSnapshot('streaming_timeout');
                    streamEnded = true; // 标记结束
                    // 发送错误信息或仅结束
                    if (!res.writableEnded) {
                         // 可以考虑发送一个错误chunk，但 OpenAI 协议没有标准错误chunk
                         res.end(); // 直接结束流
                    }
                    break; // 跳出循环
                }

                // 检查加载指示器是否消失
                const isSpinnerHidden = await loadingSpinner.isHidden({ timeout: 100 }); // 短暂检查

                if (isSpinnerHidden) {
                     // 检测到 spinner 消失后，不立即结束，再轮询一小段时间确保内容完全渲染
                     console.log('   检测到加载指示器消失，进入缓冲和最后检查阶段...');
                     const bufferEndTime = Date.now() + POST_COMPLETION_BUFFER * 2; // 给缓冲期设个结束时间
                     while(Date.now() < bufferEndTime) {
                        await new Promise(resolve => setTimeout(resolve, POLLING_INTERVAL / 2)); // 更频繁地检查
                        const currentText = await getCurrentText(responseElement, previousText);
                        if (currentText !== previousText) {
                             const delta = currentText.substring(previousText.length);
                             sendStreamChunk(res, delta);
                             previousText = currentText;
                             lastChunkSentTime = Date.now();
                         }
                     }
                     console.log('   缓冲结束，准备发送 [DONE]。');
                     streamEnded = true; // 标记结束
                     break; // 跳出主循环
                }

                // 获取当前文本并发送增量
                 const currentText = await getCurrentText(responseElement, previousText);

                if (currentText !== previousText) {
                    const delta = currentText.substring(previousText.length);
                     sendStreamChunk(res, delta);
                    previousText = currentText;
                    lastChunkSentTime = Date.now(); // 更新发送时间
                }

                // 添加一个空闲超时检测：如果长时间没有新内容且 spinner 仍在，可能卡住了
                if (Date.now() - lastChunkSentTime > 30000 && !isSpinnerHidden) { // 30秒无新内容
                    console.warn('   警告：超过30秒未收到新内容，但加载指示器仍在。可能已卡住。');
                     await saveErrorSnapshot('streaming_stalled');
                    // 可以选择在此处中断，或者继续等待 spinner 消失或总超时
                    // streamEnded = true; // 如果选择中断
                    // break;
                }


                await new Promise(resolve => setTimeout(resolve, POLLING_INTERVAL)); // 轮询间隔
            }

            // 发送 [DONE] 信号
            if (!res.writableEnded) { // 确保流还可写
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
                 await new Promise(resolve => setTimeout(resolve, POST_COMPLETION_BUFFER)); // 短暂缓冲
            } catch (timeoutError) {
                 console.error(`❌ 等待加载指示器消失超时或出错！可能回复未完成或 spinner 查找失败。`);
                 await saveErrorSnapshot('spinner_hide_timeout');
                 // 即使超时，仍然尝试获取当前内容
                 // 注意：如果 spinner 从未出现，这里也会报错，因为 loadingSpinner 可能无效
            }


            console.log('  - 获取最终完整回复文本...');
             // 重新定位最新的回复容器，以防万一
             lastResponseContainer = page.locator(responseContainerSelector).last();
             responseElement = lastResponseContainer.locator(responseTextSelector);

             let aiResponseText = null;
             const textFetchTimeout = 15000; // 获取最终文本的超时
             const maxRetries = 3;
             let attempts = 0;

             while (attempts < maxRetries && aiResponseText === null) {
                 attempts++;
                 console.log(`    - 尝试获取最终文本 (第 ${attempts} 次)...`);
                 try {
                      // 等待元素附加到DOM，并稍微可见
                      await responseElement.waitFor({ state: 'attached', timeout: 5000 });
                      // await responseElement.waitFor({ state: 'visible', timeout: 5000 }); // visible 可能过于严格

                      // 优先尝试 textContent
                      aiResponseText = await responseElement.textContent({ timeout: textFetchTimeout });
                      if (aiResponseText !== null && aiResponseText.trim() !== '') {
                           console.log("    - 成功获取 textContent。");
                           break; // 获取成功，跳出重试
                      } else {
                           console.warn("    - textContent 为空或仅空白，尝试 innerText...");
                           aiResponseText = await responseElement.innerText({ timeout: textFetchTimeout });
                           if (aiResponseText !== null && aiResponseText.trim() !== '') {
                              console.log("    - 成功获取 innerText。");
                              break;
                           } else {
                                console.warn("    - innerText 也为空或仅空白。");
                                aiResponseText = null; // 重置为 null 继续尝试或失败
                           }
                      }
                  } catch (e) {
                      console.warn(`    - 第 ${attempts} 次获取文本失败: ${e.message.split('\n')[0]}`);
                      if (attempts < maxRetries) {
                           await new Promise(resolve => setTimeout(resolve, 1000)); // 重试前等待
                      } else {
                           // 最后尝试获取整个容器的文本
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
                    finish_reason: 'stop', // 'stop' 表示正常结束
                }],
                usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, // 无法精确计算
            };
            res.json(responsePayload);
        }

        clearTimeout(operationTimer); // 清除整体超时计时器

    } catch (error) {
        clearTimeout(operationTimer); // 出错时也要清除计时器
        console.error(`❌ 处理 API 请求时出错: ${error.message}`);
        // 记录错误快照 (已在特定失败点调用 saveErrorSnapshot，这里可以作为备用)
        await saveErrorSnapshot(`general_error_${Date.now()}`);

        // 返回错误响应
        if (!res.headersSent) {
            if (isStreaming) {
                 // 对 SSE，最好不要改变 Content-Type 发送 JSON，客户端可能无法处理
                 // 遵循 OpenAI 错误格式，但通过 SSE 发送可能非标准
                 const errorPayload = { error: { message: error.message, type: 'server_error' } };
                 try {
                      res.write(`data: ${JSON.stringify(errorPayload)}\n\n`);
                      res.write('data: [DONE]\n\n'); // 即使出错也发送 DONE
                      res.end();
                 } catch(e) {
                      if (!res.writableEnded) res.end(); // 写入失败则结束
                 }
            } else {
                res.status(500).json({ error: { message: error.message, type: 'server_error' } });
            }
        } else if (isStreaming && !res.writableEnded) {
             res.end(); // 如果流式头已发，只能结束流
        }
    }
});

// --- Helper: 获取当前文本 (用于流式) ---
async function getCurrentText(responseElement, previousText) {
    try {
         // 尝试等待元素附加，但不强制 visible，因为内容可能正在快速更新
         await responseElement.waitFor({ state: 'attached', timeout: 3000 });
         return await responseElement.textContent({ timeout: 5000 }) || "";
    } catch (e) {
         // 忽略获取文本时的瞬时错误，可能是 DOM 正在更新
         // console.warn(`    (流式) 获取 textContent 时出现临时错误: ${e.message.split('\n')[0]}`);
         return previousText; // 返回上次的文本，防止发送空 delta 或丢失内容
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
        res.write(`data: ${JSON.stringify(chunk)}\n\n`);
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

          await page.screenshot({ path: screenshotPath, fullPage: true, timeout: 10000 });
          fs.writeFileSync(htmlPath, await page.content({timeout: 10000}));
          console.log(`   错误快照已保存到: ${screenshotPath}`);
          console.log(`   错误页面HTML已保存到: ${htmlPath}`);
     } catch (captureError) {
          console.error(`   尝试保存错误快照失败: ${captureError.message}`);
     }
}


// --- 启动服务器 ---
(async () => {
    await initializePlaywright(); // 启动时初始化

    const server = app.listen(SERVER_PORT, () => { // 保存 server 实例
        console.log(`\n🚀 OpenAI API 代理服务器(v2.3)正在监听 http://localhost:${SERVER_PORT}`);
        if (isPlaywrightReady) {
            console.log('✅ Playwright 已连接，服务器准备就绪。');
        } else {
            console.warn('⚠️ Playwright 未能成功初始化。API 请求将失败，直到连接成功。请检查 Chrome 和 auto_connect_aistudio.js 的运行状态。');
        }
        console.log(`确保 Chrome (由 auto_connect_aistudio.js 启动并监听端口 ${CHROME_DEBUGGING_PORT}) 正在运行...`);
    });

    // 添加更健壮的关闭处理
    const shutdown = async (signal) => {
        console.log(`\n收到 ${signal} 信号，正在关闭服务器...`);
        isShuttingDown = true; // 设置标志，阻止新请求处理（如果需要）

        // 1. 关闭 Express 服务器，停止接受新连接
        server.close(async (err) => {
            if (err) {
                console.error("关闭 HTTP 服务器时出错:", err);
            } else {
                console.log("HTTP 服务器已关闭。");
            }

            // 2. 断开 Playwright 连接 (如果存在且连接着)
            // 注意：通过 connectOverCDP 连接的 browser 没有 .close() 或 .disconnect() 方法
            // 我们依赖于进程退出时连接的自动清理
            if (browser && browser.isConnected()) {
                 console.log("Playwright 连接将随进程退出自动断开。");
                 // 如果是 launch() 启动的，则需要 browser.close()
            } else {
                 console.log("Playwright 连接不存在或已断开。");
            }

            console.log('服务器优雅关闭完成。');
            process.exit(err ? 1 : 0); // 如果关闭服务器出错，则以错误码退出
        });

        // 如果服务器在一定时间内没有关闭，强制退出
        setTimeout(() => {
            console.error("强制关闭超时，强制退出进程。");
            process.exit(1);
        }, 10000); // 10秒超时
    };

    let isShuttingDown = false;
    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));

})();