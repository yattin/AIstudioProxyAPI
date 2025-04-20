#!/usr/bin/env node

// auto_connect_aistudio.js (v2.7 - Refined Launch & Page Handling)

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

// --- Configuration ---
const DEBUGGING_PORT = 8848;
const TARGET_URL = 'https://aistudio.google.com/prompts/new_chat'; // Target page
const SERVER_SCRIPT_FILENAME = 'server.cjs'; // Corrected script name
const CONNECTION_RETRIES = 5;
const RETRY_DELAY_MS = 4000;
const CONNECT_TIMEOUT_MS = 20000; // Timeout for connecting to CDP
const NAVIGATION_TIMEOUT_MS = 35000; // Increased timeout for page navigation
const CDP_ADDRESS = `http://127.0.0.1:${DEBUGGING_PORT}`;

// --- Globals ---
const SERVER_SCRIPT_PATH = path.join(__dirname, SERVER_SCRIPT_FILENAME);
let playwright; // Loaded in checkDependencies

// --- Platform-Specific Chrome Path ---
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
                '/opt/google/chrome/chrome',
                // Add path for Flatpak installation if needed
                // '/var/lib/flatpak/exports/bin/com.google.Chrome'
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
        console.error(`❌ 错误: 未在当前目录下找到 '${SERVER_SCRIPT_FILENAME}' 文件。`);
        console.error(`   预期路径: ${SERVER_SCRIPT_PATH}`);
        console.error(`请确保 '${SERVER_SCRIPT_FILENAME}' 与此脚本位于同一目录。`);
        return false;
    }
    console.log(`✅ '${SERVER_SCRIPT_FILENAME}' 文件存在。`);

    playwright = require('playwright');
    console.log('✅ 所有依赖检查通过。');
    return true;
}

// --- 步骤 2: 检查并启动 Chrome ---
async function launchChrome() {
    console.log('-------------------------------------------------');
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

    console.log(`正在尝试启动 Chrome...`);
    console.log(`  路径: "${chromeExecutablePath}"`);
    console.log(`  参数: --remote-debugging-port=${DEBUGGING_PORT}`);

    try {
        const chromeProcess = spawn(
            chromeExecutablePath,
            [`--remote-debugging-port=${DEBUGGING_PORT}`],
            { detached: true, stdio: 'ignore' } // Detach to allow script to exit independently if needed
        );
        chromeProcess.unref(); // Allow parent process to exit independently

        console.log('✅ Chrome 启动命令已发送。稍后将尝试连接...');
        // Removed the second askQuestion - relying on connection retries now.
        // Add a small fixed delay to give Chrome a moment to start listening
        console.log('⏳ 等待 3 秒让 Chrome 进程启动...');
        await new Promise(resolve => setTimeout(resolve, 3000));
        return true;

    } catch (error) {
        console.error(`❌ 启动 Chrome 时出错: ${error.message}`);
        console.error(`   请检查路径 "${chromeExecutablePath}" 是否正确，以及是否有权限执行。`);
        return false;
    }
}

// --- 步骤 3: 连接 Playwright 并管理页面 (带重试) ---
async function connectAndManagePage() {
    console.log('-------------------------------------------------');
    console.log(`--- 步骤 3: 连接 Playwright 到 ${CDP_ADDRESS} (最多尝试 ${CONNECTION_RETRIES} 次) ---`);
    let browser = null;
    let context = null;

    for (let i = 0; i < CONNECTION_RETRIES; i++) {
        try {
            console.log(`\n尝试连接 Playwright (第 ${i + 1}/${CONNECTION_RETRIES} 次)...`);
            browser = await playwright.chromium.connectOverCDP(CDP_ADDRESS, { timeout: CONNECT_TIMEOUT_MS });
            console.log(`✅ 成功连接到 Chrome！`);

             // Simplified context fetching
             await new Promise(resolve => setTimeout(resolve, 500)); // Short delay after connect
             const contexts = browser.contexts();
             if (contexts && contexts.length > 0) {
                 context = contexts[0];
                 console.log(`-> 获取到浏览器默认上下文。`);
                 break; // Connection and context successful
             } else {
                 // This case should be rare if connectOverCDP succeeded with a responsive Chrome
                 throw new Error('连接成功，但无法获取浏览器上下文。Chrome 可能没有响应或未完全初始化。');
             }

        } catch (error) {
            console.warn(`   连接尝试 ${i + 1} 失败: ${error.message.split('\n')[0]}`);
             if (browser && browser.isConnected()) {
                 // Should not happen if connectOverCDP failed, but good practice
                 await browser.close().catch(e => console.error("尝试关闭连接失败的浏览器时出错:", e));
             }
             browser = null;
             context = null;

            if (i < CONNECTION_RETRIES - 1) {
                console.log(`   可能原因: Chrome 未完全启动 / 端口 ${DEBUGGING_PORT} 未监听 / 端口被占用。`);
                console.log(`   等待 ${RETRY_DELAY_MS / 1000} 秒后重试...`);
                await new Promise(resolve => setTimeout(resolve, RETRY_DELAY_MS));
            } else {
                console.error(`\n❌ 在 ${CONNECTION_RETRIES} 次尝试后仍然无法连接。`);
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
    console.log('\n--- 页面管理 ---');
    try {
        let targetPage = null;
        let pages = [];
        try {
            pages = context.pages();
        } catch (err) {
             console.error("❌ 获取现有页面列表时出错:", err);
             console.log("   将尝试打开新页面...");
        }

        console.log(`-> 检查 ${pages.length} 个已存在的页面...`);
        const aiStudioUrlPattern = 'aistudio.google.com/';
        const loginUrlPattern = 'accounts.google.com/';

        for (const page of pages) {
            try {
                if (!page.isClosed()) {
                    const pageUrl = page.url();
                    console.log(`   检查页面: ${pageUrl}`);
                    // Prioritize AI Studio pages, then login pages
                    if (pageUrl.includes(aiStudioUrlPattern)) {
                         console.log(`-> 找到 AI Studio 页面: ${pageUrl}`);
                         targetPage = page;
                         // Ensure it's the target URL if possible
                         if (!pageUrl.includes('/prompts/new_chat')) {
                              console.log(`   非目标页面，尝试导航到 ${TARGET_URL}...`);
                              try {
                                   await targetPage.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: NAVIGATION_TIMEOUT_MS });
                                   console.log(`   导航成功: ${targetPage.url()}`);
                              } catch (navError) {
                                   console.warn(`   警告：导航到 ${TARGET_URL} 失败: ${navError.message.split('\n')[0]}`);
                                   console.warn(`   将使用当前页面 (${pageUrl})，请稍后手动确认。`);
                              }
                         } else {
                              console.log(`   页面已在目标路径或子路径。`);
                         }
                         break; // Found a good AI Studio page
                    } else if (pageUrl.includes(loginUrlPattern) && !targetPage) {
                        // Keep track of a login page if no AI studio page is found yet
                        console.log(`-> 发现 Google 登录页面，暂存。`);
                        targetPage = page;
                        // Don't break here, keep looking for a direct AI Studio page
                    }
                 }
             } catch (pageError) {
                  if (!page.isClosed()) {
                      console.warn(`   警告：评估或导航页面时出错: ${pageError.message.split('\n')[0]}`);
                  }
                  // Avoid using a page that caused an error
                  if (targetPage === page) {
                      targetPage = null;
                  }
             }
        }

        // If after checking all pages, the best we found was a login page
        if (targetPage && targetPage.url().includes(loginUrlPattern)) {
            console.log(`-> 未找到直接的 AI Studio 页面，将使用之前找到的登录页面。`);
            console.log(`   请确保在该页面手动完成登录。`);
        }

        // If no suitable page was found at all
        if (!targetPage) {
            console.log(`-> 未找到合适的现有页面。正在打开新页面并导航到 ${TARGET_URL}...`);
            try {
                targetPage = await context.newPage();
                console.log(`   正在导航...`);
                await targetPage.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: NAVIGATION_TIMEOUT_MS });
                console.log(`-> 新页面已打开并导航到: ${targetPage.url()}`);
            } catch (newPageError) {
                 console.error(`❌ 打开或导航新页面到 ${TARGET_URL} 失败: ${newPageError.message}`);
                 console.error("   请检查网络连接，以及 Chrome 是否能正常访问该网址。可能需要手动登录。" );
                 await browser.close().catch(e => {});
                 return false;
            }
        }

        try {
            await targetPage.bringToFront();
            console.log('-> 已尝试将目标页面置于前台。');
        } catch (bringToFrontError) {
            console.warn(`   警告：将页面置于前台失败: ${bringToFrontError.message.split('\n')[0]}`);
            console.warn(`   (这可能发生在窗口最小化或位于不同虚拟桌面上时，通常不影响连接)`);
        }
        await new Promise(resolve => setTimeout(resolve, 500)); // Small delay after bringToFront


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
    console.log('-------------------------------------------------');
    console.log(`--- 步骤 4: 启动 API 服务器 ('node ${SERVER_SCRIPT_FILENAME}') ---`);
    console.log(`   脚本路径: ${SERVER_SCRIPT_PATH}`);

    if (!fs.existsSync(SERVER_SCRIPT_PATH)) {
        console.error(`❌ 错误: 无法启动服务器，文件不存在: ${SERVER_SCRIPT_PATH}`);
        process.exit(1);
    }

    console.log(`正在启动: node ${SERVER_SCRIPT_PATH}`);

    try {
        const serverProcess = spawn('node', [SERVER_SCRIPT_PATH], {
            stdio: 'inherit',
            cwd: __dirname
        });

        serverProcess.on('error', (err) => {
            console.error(`❌ 启动 '${SERVER_SCRIPT_FILENAME}' 失败: ${err.message}`);
            console.error(`请检查 Node.js 是否已安装并配置在系统 PATH 中，以及 '${SERVER_SCRIPT_FILENAME}' 文件是否有效。`);
            process.exit(1);
        });

        serverProcess.on('exit', (code, signal) => {
            console.log(`\n👋 '${SERVER_SCRIPT_FILENAME}' 进程已退出 (代码: ${code}, 信号: ${signal})。`);
            console.log("自动连接脚本执行结束。");
            process.exit(code ?? 0);
        });

        console.log("✅ '${SERVER_SCRIPT_FILENAME}' 已启动。脚本将保持运行，直到服务器进程结束或被手动中断。");

    } catch (error) {
        console.error(`❌ 启动 '${SERVER_SCRIPT_FILENAME}' 时发生意外错误: ${error.message}`);
        process.exit(1);
    }
}


// --- 主执行流程 ---
(async () => {
    console.log('🚀 欢迎使用 AI Studio 自动连接与启动脚本 (跨平台优化) v2.7 🚀');
    console.log('=================================================');

    if (!await checkDependencies()) {
        process.exit(1);
    }

    console.log('=================================================');

    if (!await launchChrome()) {
        process.exit(1);
    }

    console.log('=================================================');

    if (!await connectAndManagePage()) {
        process.exit(1);
    }

    console.log('=================================================');
    startApiServer();

})(); 