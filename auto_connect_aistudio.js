#!/usr/bin/env node

// auto_connect_aistudio.js (v2.4 - Clarified manual server start)

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
            console.log('请在当前目录下打开终端，运行以下命令来安装依赖:');
            // v2.7 Note: Added 'cors' for server.js
            console.log('\nnpm install express playwright @playwright/test cors\n');
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
        console.log('⏳ 请等待几秒钟，让 Chrome 完全启动...');
        await new Promise(resolve => setTimeout(resolve, 5000));
        await askQuestion('请确认 Chrome 窗口已出现并加载（可能需要登录Google, 并确保位于 new_chat 页面），然后按 Enter 键继续连接...');
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
    let browser = null;
    let context = null;

    for (let i = 0; i < CONNECTION_RETRIES; i++) {
        try {
            console.log(`尝试连接 Playwright (第 ${i + 1}/${CONNECTION_RETRIES} 次)...`);
            browser = await playwright.chromium.connectOverCDP(CDP_ADDRESS, { timeout: 15000 });
            console.log(`✅ 成功连接到 Chrome！`);

             await new Promise(resolve => setTimeout(resolve, 500));
             const contexts = browser.contexts();
             if (!contexts || contexts.length === 0) {
                 console.warn("   未能立即获取上下文，稍后重试...");
                 await new Promise(resolve => setTimeout(resolve, 1500));
                 const retryContexts = browser.contexts();
                 if (!retryContexts || retryContexts.length === 0) {
                      throw new Error('无法获取浏览器上下文。');
                 }
                 context = retryContexts[0];
             } else {
                 context = contexts[0];
             }
             console.log('-> 获取到浏览器上下文。');
             break; // 连接和获取上下文都成功

        } catch (error) {
            console.warn(`   连接或获取上下文尝试 ${i + 1} 失败: ${error.message.split('\n')[0]}`);
             browser = null;
             context = null;

            if (i < CONNECTION_RETRIES - 1) {
                console.log(`   等待 ${RETRY_DELAY / 1000} 秒后重试...`);
                await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
            } else {
                console.error(`❌ 在 ${CONNECTION_RETRIES} 次尝试后仍然无法连接或获取上下文。`);
                console.error('   请再次检查：');
                console.error('   1. Chrome 是否真的已经通过脚本成功启动，并且窗口可见、已加载？');
                console.error(`   2. 是否有其他程序占用了端口 ${DEBUGGING_PORT}？(可以使用命令 lsof -i :${DEBUGGING_PORT} 检查)`);
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
        const pages = context.pages();
        console.log(`-> 发现 ${pages.length} 个已存在的页面。正在搜索 AI Studio...`);
        const aiStudioUrlPattern = 'aistudio.google.com/';

        for (const page of pages) {
             try {
                if (!page.isClosed()) {
                    const pageUrl = page.url();
                    if (pageUrl.includes(aiStudioUrlPattern)) {
                         console.log(`-> 找到已存在的 AI Studio 页面: ${pageUrl}`);
                         targetPage = page;
                         // 确保导航到 new_chat 页
                         if (!pageUrl.includes('/prompts/new_chat')) {
                              console.log(`   非 new_chat 页面，正在导航到 ${TARGET_URL}...`);
                              await targetPage.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
                              console.log(`   导航完成: ${targetPage.url()}`);
                         } else {
                              console.log(`   页面已在 ${TARGET_URL} 或其子路径。`);
                         }
                         break;
                     }
                 } else {
                      console.warn('   警告：跳过一个已关闭的页面。');
                 }
             } catch (pageError) {
                  if (!page.isClosed()) {
                      console.warn(`   警告：评估或导航页面时出错: ${pageError.message.split('\n')[0]}`);
                  }
             }
        }

        if (!targetPage) {
            console.log(`-> 未找到 AI Studio 页面。正在打开新页面并导航...`);
            targetPage = await context.newPage();
            console.log(`   导航到 ${TARGET_URL}...`);
            await targetPage.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
            console.log(`-> 新页面已打开并导航到: ${targetPage.url()}`);
        }

        await targetPage.bringToFront();
        console.log('-> 已将 AI Studio 页面置于前台。');
        await new Promise(resolve => setTimeout(resolve, 1000));


        console.log('\n🎉 --- 全部完成 --- 🎉');
        console.log('Chrome 已启动，Playwright 已连接，AI Studio 页面已准备就绪。');
        console.log('请确保在 Chrome 窗口中 AI Studio 页面处于可交互状态 (例如，已登录，无弹窗)。');
        console.log('\n👉 下一步：请在另一个终端窗口中运行以下命令来启动 API 服务器：');
        console.log('\n   node server.js\n');
        console.log('让此 Chrome 窗口和服务器终端保持运行状态即可使用 API。');

        // **重要**: 不调用 disconnect。脚本退出时连接会自动关闭。
        return true;

    } catch (error) {
        console.error('\n❌ --- 步骤 3 页面管理失败 ---');
        console.error('   在连接成功后，处理页面时发生错误:', error);
        return false;
    } finally {
         console.log("-> auto_connect_aistudio.js 脚本即将退出。");
         // 不需要手动断开 browser 连接，因为是 connectOverCDP
    }
}


// --- 主执行流程 ---
(async () => {
    console.log('🚀 欢迎使用 AI Studio 自动连接脚本 (macOS) v2.4 🚀');
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
    console.log("脚本执行成功完成。请按照提示启动 server.js。");
    process.exit(0);

})();