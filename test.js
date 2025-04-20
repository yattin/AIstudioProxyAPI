// index.js (修改后 - 用于访问本地 server.js 代理)

// 确保已安装 OpenAI SDK: npm install openai
import OpenAI from "openai";

// --- 配置 ---
// 1. baseURL: 指向你本地运行的 server.js 代理服务器
//    server.js 监听 3000 端口，并提供 /v1 路径
const LOCAL_PROXY_URL = 'http://localhost:3000/v1'; // 确保端口号与 server.js 一致

// 2. apiKey: 对于本地代理，这个 key 不会被验证，可以填写任意字符串
const DUMMY_API_KEY = 'no-key-needed-for-local-proxy';

// 3. model: 这个模型名称会被发送到 server.js，但 server.js 会忽略它
//    实际使用的是 server.js 控制的 AI Studio 页面上的模型
const CUSTOM_MODEL_NAME = 'aistudio-via-local-proxy';

// --- 初始化 OpenAI 客户端 ---
const openai = new OpenAI({
    baseURL: LOCAL_PROXY_URL,
    apiKey: DUMMY_API_KEY,
    // 可选：增加超时时间，以防 AI Studio 响应较慢
    timeout: 360000, // 例如 6 分钟 (单位毫秒)
    maxRetries: 1,   // 本地代理可能不需要重试，设为 1 或 0
});

async function main() {
    console.log(`🚀 正在向本地代理 ${LOCAL_PROXY_URL} 发送请求...`);
    console.log(`   (请确保 server.js 正在运行，并且 auto_connect_aistudio.js 已成功连接到 Chrome 和 AI Studio 页面)`);

    try {
        const completion = await openai.chat.completions.create({
            // messages: 包含系统指令和用户提问
            messages: [
                {
                    role: "system",
                    // 核心要求：让 AI 将回复包裹在代码块中，并用中文回复
                    content: "请把回答全部内容套在```代码框```下输出给我。请务必使用中文进行回复。"
                },
                {
                    role: "user",
                    // 你实际想问的问题
                    content: "你好！简单介绍一下你自己以及你的能力。"
                    // 你可以修改这里的 content 来问其他问题
                    // 例如: content: "给我写一首关于月亮的七言绝句。"
                    // 例如: content: "解释一下什么是机器学习？"
                }
            ],
            // model: 指定一个名称，虽然本地代理会忽略它
            model: CUSTOM_MODEL_NAME,
            // stream: false (默认) - 等待完整回复
            // 如果你想使用流式输出，改为 stream: true，并相应处理响应事件流
            // stream: true,

            // 可以传递一些 OpenAI 不支持但你的模型可能理解的额外参数（server.js 目前不处理）
            // temperature: 0.7, // 示例
        });

        console.log("\n✅ --- 来自本地代理 (AI Studio) 的回复 --- ✅");

        // 处理非流式响应
        if (completion && completion.choices && completion.choices.length > 0) {
             const messageContent = completion.choices[0].message?.content;
             if (messageContent) {
                console.log(messageContent);
             } else {
                console.log("收到了回复，但消息内容为空。");
                console.log("原始回复对象:", JSON.stringify(completion, null, 2));
             }
        } else {
            console.log("未能从代理获取有效的回复结构。");
            console.log("原始回复对象:", JSON.stringify(completion, null, 2));
        }
        console.log("----------------------------------------------\n");

    } catch (error) {
        console.error("\n❌ --- 请求出错 --- ❌");
        if (error instanceof OpenAI.APIError) {
            console.error(`   错误类型: OpenAI APIError (可能是代理返回的错误)`);
            console.error(`   状态码: ${error.status}`);
            console.error(`   错误消息: ${error.message}`);
            console.error(`   错误代码: ${error.code}`);
            console.error(`   错误参数: ${error.param}`);
            console.error(`   完整错误:`, error);
        } else if (error.code === 'ECONNREFUSED') {
            console.error(`   错误类型: 连接被拒绝 (ECONNREFUSED)`);
            console.error(`   无法连接到服务器 ${LOCAL_PROXY_URL}。`);
            console.error("   请检查：");
            console.error("   1. server.js 是否已启动并正在监听指定的端口？");
            console.error("   2. 防火墙设置是否允许本地连接？");
        } else if (error.name === 'TimeoutError' || (error.cause && error.cause.code === 'UND_ERR_CONNECT_TIMEOUT')) {
             console.error(`   错误类型: 连接超时`);
             console.error(`   连接到 ${LOCAL_PROXY_URL} 超时。`);
             console.error("   请检查 server.js 是否运行正常，以及网络状况。AI Studio 响应可能过慢。");
        } else {
            // 其他类型的错误 (例如网络问题, 请求设置错误)
            console.error('   发生了未知错误:', error.message);
            console.error('   错误详情:', error);
        }
        console.error("----------------------------------------------\n");
    }
}

// --- 运行主函数 ---
// 在运行前，请确保：
// 1. 你已经按照 auto_connect_aistudio.js 的指引启动了 Chrome 并连接成功。
// 2. 你已经在另一个终端运行了 `node server.js` 并且它显示正在监听端口 3000。
main();