// stream_test.js (v1.1 - Moved receivedDone declaration)

// --- 配置 ---
const SERVER_URL = 'http://localhost:3000'; // 你的本地服务器地址
const API_ENDPOINT = '/v1/chat/completions';
const TARGET_URL = `${SERVER_URL}${API_ENDPOINT}`;

// --- 请求体 (包含 stream: true) ---
const requestPayload = {
    // model 字段是必须的，即使你的服务器可能不使用它
    model: "google-ai-studio-via-playwright-cdp", // 与服务器 model 匹配（随便填）
    messages: [
        { role: "user", content: "请写一首关于春天的七言律诗，包含'花'和'鸟'" } // 修改为你想要的 prompt
    ],
    stream: true, // <--- 关键：开启流式响应
    // temperature: 0.7, // 可以添加其他参数
};

// --- 主测试函数 ---
async function testStreaming() {
    console.log(`🚀 开始测试流式 API: POST ${TARGET_URL}`);
    console.log('请求内容:', JSON.stringify(requestPayload, null, 2)); // 格式化输出请求体
    console.log('\n--- 流式响应 ---');

    let fullResponse = ""; // 用于累积完整响应文本
    let errorOccurred = false;
    let receivedDone = false; // <--- 移到 try 外部声明并初始化

    try {
        const response = await fetch(TARGET_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // 'Accept': 'text/event-stream' // 可以显式声明期望的类型
            },
            body: JSON.stringify(requestPayload),
        });

        if (!response.ok) {
             // 尝试读取 JSON 错误体
             let errorJson = null;
             let errorText = response.statusText; // 默认使用状态文本
             try {
                 // 需要先克隆响应体才能多次读取
                 const clonedResponse = response.clone();
                 errorJson = await response.json();
                 errorText = errorJson?.error?.message || JSON.stringify(errorJson); // 优先使用 JSON 中的错误信息
             } catch(e) {
                  try {
                      // 如果 JSON 解析失败，尝试读取文本
                      errorText = await clonedResponse.text();
                  } catch (e2) { /* 忽略读取文本的错误 */ }
             }
            errorOccurred = true; // 标记发生错误
            throw new Error(`服务器返回错误状态码: ${response.status}. 错误: ${errorText}`);
        }

        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('text/event-stream')) {
            console.warn(`⚠️ 警告: 响应的 Content-Type 不是 'text/event-stream' (收到: ${contentType})。`);
             errorOccurred = true; // 非流式响应也视为测试目标失败
             // 如果不是流式，尝试读取 JSON 或文本
             try {
                 const bodyText = await response.text();
                 try {
                      const jsonBody = JSON.parse(bodyText);
                      console.log("非流式响应内容 (JSON):", JSON.stringify(jsonBody, null, 2));
                 } catch (e) {
                      console.log("非流式响应内容 (Text):", bodyText);
                 }
             } catch (e) {
                  console.error("读取非流式响应体时出错:", e);
             }
            return; // 结束测试
        }

        // 确认是流式响应后才开始处理
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        // let receivedDone = false; // <--- 从这里移除

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                console.log("\n(Reader finished reading stream)"); // 调试信息
                break; // 读取完毕
            }

            buffer += decoder.decode(value, { stream: true });
            // console.log("Raw buffer chunk:", buffer); // 调试原始数据块
            let lines = buffer.split('\n');
            buffer = lines.pop() || ''; // 保留最后不完整的一行

            for (const line of lines) {
                 const trimmedLine = line.trim();
                 // console.log("Processing line:", trimmedLine); // 调试每一行
                if (trimmedLine === '') continue; // 忽略空行

                if (trimmedLine.startsWith('data:')) {
                    const dataContent = trimmedLine.substring(5).trim();

                    if (dataContent === '[DONE]') {
                        receivedDone = true; // 标记收到 DONE
                        console.log('\n[收到 DONE 信号]');
                        break; // 收到 DONE 就不用再处理这一批的后续行了
                    }

                    try {
                        const jsonData = JSON.parse(dataContent);
                        // 处理可能的错误块 (服务器在流中发送错误JSON)
                        if (jsonData.error) {
                             console.error(`\n❌ 服务器流式传输错误: ${jsonData.error.message || JSON.stringify(jsonData.error)}`);
                             errorOccurred = true;
                             break; // 收到错误，停止处理
                        }

                        const deltaContent = jsonData.choices?.[0]?.delta?.content;
                        if (deltaContent) {
                            process.stdout.write(deltaContent); // 直接打印到控制台，模拟打字效果
                            fullResponse += deltaContent; // 累积完整响应
                        } else if (jsonData.choices?.[0]?.delta && Object.keys(jsonData.choices[0].delta).length === 0) {
                            // 处理空 delta 对象 {}，这有时表示流的开始
                            // console.log("[收到空 delta]");
                        } else {
                            // 收到非预期的 data 结构
                            // console.warn(`\n[收到未知结构的 data]: ${dataContent}`);
                        }

                    } catch (parseError) {
                        console.error(`\n❌ JSON 解析错误: ${parseError.message}`);
                        console.error(`   原始数据行: "${line}"`);
                        errorOccurred = true;
                        break; // 解析错误，停止处理
                    }
                } else {
                    // 收到非 data: 开头的行，可能是注释或意外内容
                    // console.warn(`\n[收到非 data 行]: "${trimmedLine}"`);
                }
            }
             if (receivedDone || errorOccurred) break; // 如果收到 DONE 或出错，跳出外层 while 循环
        }

         // 解码缓冲区中最后剩余的部分
         if (buffer.trim()) {
              // 理论上，在 [DONE] 之后缓冲区应该为空或只包含空白符
              console.warn("\n⚠️ 警告: 流结束后缓冲区仍有残留数据:", buffer);
              // 可以尝试处理这部分残留数据，以防万一
              if (buffer.startsWith('data:')) {
                  // 尝试处理逻辑同上
              }
         }

    } catch (error) {
        // 捕获 fetch 本身的错误或 response.ok 检查抛出的错误
        console.error('\n❌ 测试过程中发生网络或协议错误:', error);
        errorOccurred = true;
    } finally {
         process.stdout.write('\n'); // 确保最后有换行
        if (!errorOccurred) {
            console.log('\n--- 流处理完毕 ---');
            if (!receivedDone) {
                 console.warn('⚠️ 警告: 流已结束，但未收到明确的 [DONE] 信号。响应可能不完整。');
            }
            console.log('\n完整响应文本:');
            console.log(fullResponse || '(空响应)'); // 如果 fullResponse 为空也明确提示
        } else {
            console.log('\n--- 测试因错误中断或未按预期完成 ---');
        }
    }
}

testStreaming();