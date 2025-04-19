// stream_test.js

// --- 配置 ---
const SERVER_URL = 'http://localhost:3000'; // 你的本地服务器地址
const API_ENDPOINT = '/v1/chat/completions';
const TARGET_URL = `${SERVER_URL}${API_ENDPOINT}`;

// --- 请求体 (包含 stream: true) ---
const requestPayload = {
    // model 字段是必须的，即使你的服务器可能不使用它
    model: "google-ai-studio-via-playwright-cdp",
    messages: [
        { role: "user", content: "请写一首关于春天的短诗" }
        // 你可以修改这里的 prompt
    ],
    stream: true, // <--- 关键：开启流式响应
    // 可以添加其他 OpenAI 支持的参数，如 temperature, max_tokens (服务器需要支持处理它们)
    // temperature: 0.7,
};

// --- 主测试函数 ---
async function testStreaming() {
    console.log(`🚀 开始测试流式 API: POST ${TARGET_URL}`);
    console.log('请求内容:', JSON.stringify(requestPayload));
    console.log('\n--- 流式响应 ---');

    let fullResponse = ""; // 用于累积完整响应文本
    let errorOccurred = false;

    try {
        const response = await fetch(TARGET_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // 如果你的 API 需要认证，在这里添加 'Authorization': 'Bearer YOUR_API_KEY'
            },
            body: JSON.stringify(requestPayload),
        });

        // 检查初始 HTTP 状态码
        if (!response.ok) {
             const errorBody = await response.text(); // 尝试读取错误信息
            throw new Error(`服务器返回错误状态码: ${response.status} ${response.statusText}\n错误详情: ${errorBody}`);
        }

        // 检查 Content-Type 是否为 text/event-stream
        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('text/event-stream')) {
            console.warn(`⚠️ 警告: 响应的 Content-Type 不是 'text/event-stream' (收到: ${contentType})。可能不是有效的 SSE 流。`);
        }

        // 处理流式响应体
        const reader = response.body.getReader();
        const decoder = new TextDecoder(); // 用于将 Uint8Array 转换为字符串
        let buffer = ''; // 用于处理跨数据块的 SSE 消息

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                // console.log('\n[流结束]'); // 流自然结束
                break;
            }

            // 将接收到的数据块解码并添加到缓冲区
            buffer += decoder.decode(value, { stream: true });

            // 按行处理缓冲区中的数据
            let lines = buffer.split('\n');

            // 最后一行可能不完整，留在缓冲区等待下一个数据块
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.trim() === '') {
                    // 忽略空行 (SSE 消息间的分隔符)
                    continue;
                }

                if (line.startsWith('data:')) {
                    const dataContent = line.substring(5).trim(); // 移除 "data: " 前缀并去除前后空格

                    if (dataContent === '[DONE]') {
                        // console.log('\n[收到 DONE 信号]');
                        // 可以认为流在这里正常结束了，即使 reader.read() 还没 done
                        // 通常 [DONE] 后面就不会有有效数据了
                         process.stdout.write('\n'); // 确保最后有换行
                         console.log('--- 流处理完毕 ---');
                         console.log('\n完整响应:');
                         console.log(fullResponse);
                         return; // 正常结束测试
                    }

                    try {
                        const jsonData = JSON.parse(dataContent);
                        // 提取增量内容
                        const deltaContent = jsonData.choices?.[0]?.delta?.content;
                        if (deltaContent) {
                            process.stdout.write(deltaContent); // 打印增量内容，不换行
                            fullResponse += deltaContent; // 累积完整响应
                        }
                        // 可以选择性打印 finish_reason 等其他信息
                        // const finishReason = jsonData.choices?.[0]?.finish_reason;
                        // if (finishReason) {
                        //     console.log(`\n[结束原因: ${finishReason}]`);
                        // }
                    } catch (parseError) {
                        console.error(`\n❌ JSON 解析错误: ${parseError.message}`);
                        console.error(`   原始数据行: "${line}"`);
                        errorOccurred = true; // 标记发生错误
                        // 不中断循环，尝试继续处理后续行
                    }
                } else {
                    // 忽略非 data: 开头的行 (例如注释行 : xxx)
                    // console.log(`[忽略行: ${line}]`);
                }
            }
        }
        // 如果循环结束但没收到 [DONE]，可能是服务器实现不标准
         if (!errorOccurred && !fullResponse.endsWith('[DONE]')) { // 确保没收到 DONE 才警告
            console.warn('\n⚠️ 警告: 流已结束，但未收到明确的 [DONE] 信号。');
         }


    } catch (error) {
        console.error('\n❌ 测试过程中发生错误:', error);
        errorOccurred = true;
    } finally {
        if (!errorOccurred) {
             // 如果前面没有正常返回 (比如没收到 [DONE] 但流结束了)
             if (!fullResponse.endsWith('\n--- 流处理完毕 ---')){ // 避免重复打印
                 process.stdout.write('\n'); // 确保最后有换行
                 console.log('--- 流处理完毕 (可能未收到 DONE 信号) ---');
                 console.log('\n完整响应:');
                 console.log(fullResponse);
             }
        } else {
            console.log('\n--- 测试因错误中断 ---');
        }
    }
}

// --- 运行测试 ---
testStreaming();