#!/usr/bin/env node

// auto_connect_aistudio.js (v2.6 - Platform Compatibility & Launch Optimization)

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

// --- 配置 ---
const DEBUGGING_PORT = 8848;
const CDP_ADDRESS = `http://127.0.0.1:${DEBUGGING_PORT}`;
const TARGET_URL = 'https://aistudio.google.com/prompts/new_chat';
const SERVER_SCRIPT_PATH = path.join(__dirname, 'server.js');
const CONNECTION_RETRIES = 5; // 稍微增加重试次数以适应不同的启动时间
const RETRY_DELAY = 4000;
let playwright;

// --- 平台相关的 Chrome 路径 ---
function getChromePath() {
    switch (process.platform) {
        case 'darwin':
            return '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
        case 'win32':
            // 尝试 Program Files 和 Program Files (x86)
            const winPaths = [
                path.join(process.env.ProgramFiles || '', 'Google\Chrome\Application\chrome.exe'),
                path.join(process.env['ProgramFiles(x86)'] || '', 'Google\Chrome\Application\chrome.exe')
            ];
            return winPaths.find(p => fs.existsSync(p));
        case 'linux':
            // 尝试常见的 Linux 路径
            const linuxPaths = [
                '/usr/bin/google-chrome',
                '/usr/bin/google-chrome-stable',
                '/opt/google/chrome/chrome'
            ];
            return linuxPaths.find(p => fs.existsSync(p));
        default:
            return null; // 不支持的平台
    }
}

const chromeExecutablePath = getChromePath();

// --- 创建 Readline Interface ---
function askQuestion(query) {
    const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
    });

    return new Promise(resolve => rl.question(query, ans => {
        rl.close();
        resolve(ans);
    }))
}

// --- 步骤 1: 检查 Playwright 依赖 ---
async function checkDependencies() {
    console.log('--- 步骤 1: 检查依赖 (Express, Playwright, @playwright/test, CORS) ---');
    const requiredModules = ['express', 'playwright', '@playwright/test', 'cors'];
    const missingModules = [];

    for (const moduleName of requiredModules) {
        try {
            require(moduleName);
            console.log(`✅ 依赖 '${moduleName}' 已安装。`);
        } catch (error) {
            if (error.code === 'MODULE_NOT_FOUND') {
                missingModules.push(moduleName);
            } else {
                console.error(`❌ 检查依赖 '${moduleName}' 时发生未知错误:`, error);
                return false;
            }
        }
    }

    if (missingModules.length > 0) {
        console.error(`❌ 错误: 缺少以下依赖: ${missingModules.join(', ')}`);
        console.log('请在当前目录下打开终端，运行以下命令来安装所有必需的依赖:');
        console.log(`
npm install express playwright @playwright/test cors
`);
        console.log('安装完成后，请重新运行此脚本。');
        return false;
    }

    if (!fs.existsSync(SERVER_SCRIPT_PATH)) {
        console.error(`❌ 错误: 未在当前目录下找到 'server.js' 文件。`);
        console.error(`   预期路径: ${SERVER_SCRIPT_PATH}`);
        console.error(`请确保 'server.js' 与此脚本位于同一目录。`);
        return false;
    }
    console.log(`✅ 'server.js' 文件存在。`);

    playwright = require('playwright');
    console.log('✅ 所有依赖检查通过。');
    return true;
}

// --- 步骤 2: 检查并启动 Chrome ---
async function launchChrome() {
    console.log(`--- 步骤 2: 启动 Chrome (调试端口 ${DEBUGGING_PORT}) ---`);

    if (!chromeExecutablePath) {
        console.error(`❌ 错误: 未能在当前操作系统 (${process.platform}) 的常见路径找到 Chrome 可执行文件。`);
        console.error('   请确保已安装 Google Chrome，或修改脚本中的 getChromePath 函数以指向正确的路径。');
        if (process.platform === 'win32') {
             console.error('   (已尝试查找 %ProgramFiles% 和 %ProgramFiles(x86)% 下的路径)');
        } else if (process.platform === 'linux') {
             console.error('   (已尝试查找 /usr/bin/google-chrome, /usr/bin/google-chrome-stable, /opt/google/chrome/chrome)');
        }
        return false;
    }

    console.log(`   找到 Chrome 路径: ${chromeExecutablePath}`);
    console.log('⚠️ 重要提示：为了确保调试端口生效，请先手动完全退出所有正在运行的 Google Chrome 实例。');
    console.log('   (在 macOS 上通常是 Cmd+Q，Windows/Linux 上是关闭所有窗口)');
    await askQuestion('请确认所有 Chrome 实例已关闭，然后按 Enter 键继续启动...');

    console.log(`正在尝试启动 Chrome: "${chromeExecutablePath}" --remote-debugging-port=${DEBUGGING_PORT}`);

    try {
        const chromeProcess = spawn(
            chromeExecutablePath,
            [`--remote-debugging-port=${DEBUGGING_PORT}`],
            { detached: true, stdio: 'ignore' }
        );
        chromeProcess.unref();

        console.log('✅ Chrome 启动命令已发送。将由后续步骤尝试连接...');
        // 移除固定的等待和用户确认，让连接重试逻辑处理
        // console.log('⏳ 请等待几秒钟，让 Chrome 完全启动...');
        // await new Promise(resolve => setTimeout(resolve, 5000));
        // await askQuestion('请确认 Chrome 窗口已出现并加载（可能需要登录Google, 并确保位于 new_chat 页面），然后按 Enter 键继续连接...');
        return true;

    } catch (error) {
        console.error(`❌ 启动 Chrome 时出错: ${error.message}`);
        console.error(`   请检查路径 "${chromeExecutablePath}" 是否正确，以及是否有权限执行。`);
        return false;
    }
}

// --- 步骤 3: 连接 Playwright 并管理页面 (带重试) ---
async function connectAndManagePage() {
    console.log(`--- 步骤 3: 连接 Playwright 到 ${CDP_ADDRESS} (最多尝试 ${CONNECTION_RETRIES} 次) ---`);
    let browser = null;
    let context = null;

    for (let i = 0; i < CONNECTION_RETRIES; i++) {
        try {
            console.log(`尝试连接 Playwright (第 ${i + 1}/${CONNECTION_RETRIES} 次)...`);
            // 稍微增加连接超时时间
            browser = await playwright.chromium.connectOverCDP(CDP_ADDRESS, { timeout: 20000 });
            console.log(`✅ 成功连接到 Chrome！`);

             // 尝试获取上下文，增加一些延迟和重试
             await new Promise(resolve => setTimeout(resolve, 1000)); // 初始等待
             let attempts = 0;
             while (attempts < 3 && (!context || context.pages().length === 0)) {
                 const contexts = browser.contexts();
                 if (contexts && contexts.length > 0) {
                     context = contexts[0];
                     console.log(`-> 获取到浏览器上下文 (尝试 ${attempts + 1})。`);
                     break;
                 }
                 attempts++;
                 if (attempts < 3) {
                    console.warn(`   未能立即获取有效上下文，${1.5 * attempts}秒后重试...`);
                    await new Promise(resolve => setTimeout(resolve, 1500 * attempts));
                 }
             }

             if (!context) {
                  throw new Error('无法获取有效的浏览器上下文。');
             }
             break; // 连接和获取上下文都成功

        } catch (error) {
            console.warn(`   连接或获取上下文尝试 ${i + 1} 失败: ${error.message.split('\n')[0]}`);
             if (browser && browser.isConnected()) {
                 await browser.close().catch(e => console.error("尝试关闭连接失败的浏览器时出错:", e)); // 确保关闭无效连接
             }
             browser = null;
             context = null;

            if (i < CONNECTION_RETRIES - 1) {
                console.log(`   等待 ${RETRY_DELAY / 1000} 秒后重试...`);
                await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
            } else {
                console.error(`❌ 在 ${CONNECTION_RETRIES} 次尝试后仍然无法连接或获取上下文。`);
                console.error('   请再次检查：');
                console.error('   1. Chrome 是否真的已经通过脚本成功启动，并且窗口可见、已加载？(可能需要登录Google)');
                console.error(`   2. 是否有其他程序占用了端口 ${DEBUGGING_PORT}？(检查命令: macOS/Linux: lsof -i :${DEBUGGING_PORT} | Windows: netstat -ano | findstr ${DEBUGGING_PORT})`);
                console.error('   3. 启动 Chrome 时终端或系统是否有报错信息？');
                console.error('   4. 防火墙或安全软件是否阻止了本地回环地址(127.0.0.1)的连接？');
                return false;
            }
        }
    }

    if (!browser || !context) {
         console.error("-> 未能成功连接到浏览器或获取上下文。");
         return false;
    }

    // --- 连接成功后的页面管理逻辑 ---
    try {
        let targetPage = null;
        let pages = [];
        try {
            pages = context.pages();
        } catch (err) {
             console.error("❌ 获取页面列表时出错:", err);
             console.log("   将尝试打开新页面...");
        }

        console.log(`-> 发现 ${pages.length} 个已存在的页面。正在搜索 AI Studio...`);
        const aiStudioUrlPattern = 'aistudio.google.com/';

        for (const page of pages) {
             try {
                if (!page.isClosed()) {
                    const pageUrl = page.url();
                    // 允许稍微宽泛的匹配，包括重定向后的 URL
                    if (pageUrl.includes(aiStudioUrlPattern) || pageUrl.startsWith('https://accounts.google.com/')) {
                         console.log(`-> 找到可能是 AI Studio 或登录相关的页面: ${pageUrl}`);
                         targetPage = page;
                         // 确保导航到 new_chat 页 (如果不是账户页)
                         if (!pageUrl.startsWith('https://accounts.google.com/') && !pageUrl.includes('/prompts/new_chat')) {
                              console.log(`   非 new_chat 页面，正在导航到 ${TARGET_URL}...`);
                              try {
                                   await targetPage.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
                                   console.log(`   导航完成: ${targetPage.url()}`);
                              } catch (navError) {
                                   console.warn(`   警告：导航到 ${TARGET_URL} 失败: ${navError.message.split('\n')[0]}`);
                                   console.warn(`   将保留当前页面 (${pageUrl})，请稍后手动确认页面内容。`);
                              }
                         } else if (pageUrl.startsWith('https://accounts.google.com/')) {
                              console.log(`   页面似乎在 Google 登录页，请手动完成登录。`);
                         }
                         else {
                              console.log(`   页面已在 ${TARGET_URL} 或其子路径。`);
                         }
                         break; // 找到目标页面或登录页，停止搜索
                     }
                 } else {
                      // console.log('   跳过一个已关闭的页面。'); // 这个日志可能过于频繁，注释掉
                 }
             } catch (pageError) {
                  if (!page.isClosed()) {
                      console.warn(`   警告：评估或导航页面 (${page.url()}) 时出错: ${pageError.message.split('\n')[0]}`);
                      console.warn(`   将忽略此页面，继续查找或创建新页面。`);
                  }
                  // 确保出错的页面不会被误用
                  if (targetPage === page) {
                      targetPage = null;
                  }
             }
        }

        if (!targetPage) {
            console.log(`-> 未找到合适的 AI Studio 页面或登录页面。正在打开新页面并导航...`);
            try {
                targetPage = await context.newPage();
                console.log(`   导航到 ${TARGET_URL}...`);
                await targetPage.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 35000 });
                console.log(`-> 新页面已打开并导航到: ${targetPage.url()}`);
            } catch (newPageError) {
                 console.error(`❌ 打开或导航新页面到 ${TARGET_URL} 失败: ${newPageError.message}`);
                 console.error("   请检查网络连接，以及 Chrome 是否能正常访问该网址。");
                 await browser.close().catch(e => {}); // 关闭浏览器
                 return false;
            }
        }

        await targetPage.bringToFront();
        console.log('-> 已将目标页面置于前台。');
        await new Promise(resolve => setTimeout(resolve, 1000));


        console.log('\n🎉 --- AI Studio 连接准备完成 --- 🎉');
        console.log('Chrome 已启动，Playwright 已连接，相关页面已找到或创建。');
        console.log('请确保在 Chrome 窗口中 AI Studio 页面处于可交互状态 (例如，已登录Google, 无弹窗)。');

        return true;

    } catch (error) {
        console.error('\n❌ --- 步骤 3 页面管理失败 ---');
        console.error('   在连接成功后，处理页面时发生错误:', error);
        if (browser && browser.isConnected()) {
             await browser.close().catch(e => console.error("关闭浏览器时出错:", e));
        }
        return false;
    } finally {
         // 这里不再打印即将退出的日志，因为脚本会继续运行 server.js
         // console.log("-> auto_connect_aistudio.js 步骤3结束。");
         // 不需要手动断开 browser 连接，因为是 connectOverCDP
    }
}


// --- 步骤 4: 启动 API 服务器 ---
function startApiServer() {
    console.log(`--- 步骤 4: 启动 API 服务器 ('node server.js') ---`);
    console.log(`正在启动: node ${SERVER_SCRIPT_PATH}`);

    try {
        const serverProcess = spawn('node', [SERVER_SCRIPT_PATH], {
            stdio: 'inherit',
            cwd: __dirname
        });

        serverProcess.on('error', (err) => {
            console.error(`❌ 启动 'server.js' 失败: ${err.message}`);
            console.error(`请检查 Node.js 是否已安装并配置在系统 PATH 中，以及 'server.js' 文件是否有效。`);
            process.exit(1);
        });

        serverProcess.on('exit', (code, signal) => {
            console.log(`\n👋 'server.js' 进程已退出 (代码: ${code}, 信号: ${signal})。`);
            console.log("自动连接脚本执行结束。");
            process.exit(code ?? 0);
        });

        console.log("✅ 'server.js' 已启动。脚本将保持运行，直到服务器进程结束或被手动中断。");

    } catch (error) {
        console.error(`❌ 启动 'server.js' 时发生意外错误: ${error.message}`);
        process.exit(1);
    }
}


// --- 主执行流程 ---
(async () => {
    console.log('🚀 欢迎使用 AI Studio 自动连接与启动脚本 (跨平台优化) v2.6 🚀');
    console.log('-------------------------------------------------');

    if (!await checkDependencies()) {
        process.exit(1);
    }

    console.log('-------------------------------------------------');

    if (!await launchChrome()) {
        process.exit(1);
    }

    console.log('-------------------------------------------------');

    if (!await connectAndManagePage()) {
        process.exit(1);
    }

    console.log('-------------------------------------------------');
    startApiServer();

})();