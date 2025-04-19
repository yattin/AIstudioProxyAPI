#!/usr/bin/env node

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

// --- 配置 ---
const DEBUGGING_PORT = 8848;
const CDP_ADDRESS = `http://127.0.0.1:${DEBUGGING_PORT}`;
const TARGET_URL = 'https://aistudio.google.com/prompts/new_chat';
const MACOS_CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CONNECTION_RETRIES = 4;
const RETRY_DELAY = 4000;
let playwright;

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
    console.log('--- 步骤 1: 检查依赖 (Playwright) ---');
    try {
        playwright = require('playwright');
        console.log('✅ Playwright 依赖已安装。');
        return true;
    } catch (error) {
        if (error.code === 'MODULE_NOT_FOUND') {
            console.error('❌错误: Playwright 依赖未找到！');
            console.log('请在当前目录下打开终端，运行以下命令来安装 Playwright:');
            console.log('\nnpm install playwright\n');
            console.log('安装完成后，请重新运行此脚本。');
        } else {
            console.error('❌ 检查依赖时发生未知错误:', error);
        }
        return false;
    }
}

// --- 步骤 2: 检查并启动 Chrome ---
async function launchChrome() {
    console.log(`--- 步骤 2: 启动 Chrome (调试端口 ${DEBUGGING_PORT}) ---`);

    if (!fs.existsSync(MACOS_CHROME_PATH)) {
        console.error(`❌ 错误: 未在默认路径找到 Chrome 可执行文件:`);
        console.error(`   ${MACOS_CHROME_PATH}`);
        console.error('请确保 Google Chrome 已安装在 /Applications 目录下，或修改脚本中的 MACOS_CHROME_PATH 指向正确的路径。');
        return false;
    }

    console.log('⚠️ 重要提示：为了确保调试端口生效，请先手动完全退出所有正在运行的 Google Chrome 实例 (Cmd+Q)。');
    await askQuestion('请确认所有 Chrome 实例已关闭，然后按 Enter 键继续...');

    console.log(`正在尝试启动 Chrome: "${MACOS_CHROME_PATH}" --remote-debugging-port=${DEBUGGING_PORT}`);

    try {
        const chromeProcess = spawn(
            MACOS_CHROME_PATH,
            [`--remote-debugging-port=${DEBUGGING_PORT}`],
            { detached: true, stdio: 'ignore' }
        );
        chromeProcess.unref();

        console.log('✅ Chrome 启动命令已发送。');
        await askQuestion('请等待 Chrome 窗口完全出现并加载后，按 Enter 键继续连接...');
        return true;

    } catch (error) {
        console.error(`❌ 启动 Chrome 时出错: ${error.message}`);
        console.error('请检查 Chrome 路径是否正确，以及是否有权限执行。');
        return false;
    }
}

// --- 步骤 3: 连接 Playwright 并管理页面 (带重试) ---
async function connectAndManagePage() {
    console.log(`--- 步骤 3: 连接 Playwright 到 ${CDP_ADDRESS} (最多尝试 ${CONNECTION_RETRIES} 次) ---`);
    let browser = null; // 将 browser 声明移到 try 块外部

    for (let i = 0; i < CONNECTION_RETRIES; i++) {
        try {
            console.log(`尝试连接 Playwright (第 ${i + 1}/${CONNECTION_RETRIES} 次)...`);
            browser = await playwright.chromium.connectOverCDP(CDP_ADDRESS, { timeout: 15000 });
            console.log(`✅ 成功连接到 Chrome！`);
            break; // 连接成功，跳出循环

        } catch (error) {
            console.warn(`   连接尝试 ${i + 1} 失败: ${error.message.split('\n')[0]}`);
            if (i < CONNECTION_RETRIES - 1) {
                console.log(`   等待 ${RETRY_DELAY / 1000} 秒后重试...`);
                await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
            } else {
                console.error(`❌ 在 ${CONNECTION_RETRIES} 次尝试后仍然无法连接。`);
                console.error('   请再次检查：');
                console.error('   1. Chrome 是否真的已经通过脚本成功启动，并且窗口可见？');
                console.error(`   2. 是否有其他程序占用了端口 ${DEBUGGING_PORT}？(可以使用命令 lsof -i :${DEBUGGING_PORT} 检查)`);
                console.error('   3. 启动 Chrome 时终端或系统是否有报错信息？');
                console.error('   4. 防火墙或安全软件是否阻止了本地回环地址(127.0.0.1)的连接？');
                return false; // 重试用尽，连接失败
            }
        }
    }

    if (!browser) {
         return false;
    }

    // --- 连接成功后的页面管理逻辑 ---
    try {
        const context = browser.contexts()[0];
        if (!context) {
            throw new Error('无法获取浏览器上下文。');
        }
        console.log('-> 获取到浏览器上下文。');

        let targetPage = null;
        const pages = context.pages();
        console.log(`-> 发现 ${pages.length} 个已存在的页面。正在搜索 AI Studio...`);

        for (const page of pages) {
             try {
                const pageUrl = page.url();
                if (pageUrl.startsWith('https://aistudio.google.com/')) {
                     console.log(`-> 找到已存在的 AI Studio 页面: ${pageUrl}`);
                     if (pageUrl !== TARGET_URL) {
                         console.log(`   正在导航到 ${TARGET_URL}...`);
                         await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
                         console.log('   导航完成。');
                     }
                     targetPage = page;
                     break;
                 }
             } catch (pageError) {
                 console.warn(`   警告：无法评估某个页面的 URL: ${pageError.message}`);
             }
        }

        if (!targetPage) {
            console.log(`-> 未找到 AI Studio 页面。正在打开新页面并导航...`);
            targetPage = await context.newPage();
            await targetPage.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
            console.log(`-> 新页面已打开并导航到: ${targetPage.url()}`);
        }

        await targetPage.bringToFront();
        console.log('-> 已将 AI Studio 页面置于前台。');

        console.log('\n🎉 --- 全部完成 --- 🎉');
        console.log('Chrome 已启动，Playwright 已连接，AI Studio 页面已准备就绪。');
        console.log('你可以手动在此 Chrome 窗口中进行登录等操作（如果需要）。');
        console.log('这个脚本的任务已完成。你可以关闭这个终端窗口，Chrome 会继续运行。');
        console.log('后续的 API 服务器脚本将需要重新连接到这个正在运行的 Chrome 实例。');

        // **修改点：移除 browser.disconnect() 调用**
        // await browser.disconnect(); // <--- 删除或注释掉这一行
        console.log('\n-> Playwright 客户端将随脚本结束自动断开连接。浏览器保持运行。');
        return true; // 整个步骤成功

    } catch (error) {
        console.error('\n❌ --- 步骤 3 页面管理失败 ---');
        console.error('   在连接成功后，处理页面时发生错误:', error);
        // **修改点：移除 browser.disconnect() 调用**
        // if (browser && browser.isConnected()) { // isConnected() 也不存在于 connectOverCDP 返回的 browser 对象上
        //     await browser.disconnect(); // <--- 删除或注释掉这一行
        // }
        return false; // 页面管理失败
    }
}


// --- 主执行流程 ---
(async () => {
    console.log('🚀 欢迎使用 AI Studio 自动连接脚本 (macOS) v2.1 🚀'); // 版本号+0.1
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
    process.exit(0); // 所有步骤成功完成

})();