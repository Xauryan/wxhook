# wx_hook_bridge HTTP API

本文档以 wxhelper `4.1.9.23.md` 为格式模板，并按当前分支 `http_api.c` 的实际实现重建。未实现接口不会出现在正文中；数据库 API 因尚无 native 方法而忽略。

## 通用说明

- 默认地址：`http://127.0.0.1:19088/api/<接口名>`
- 请求方式：除 `/api/checkLogin` 兼容 GET 外，统一使用 HTTP `POST`。
- 请求格式：`Content-Type: application/json`，UTF-8 JSON。
- 响应格式：`{"code":1,"msg":"success","data":{}}`。
- `code=1` 表示成功，`code=0` 表示失败。当前实现通常把 native 返回值归一化为 `0/1`。
- 异步接口的 `data` 可能包含 `ok`、`command`、`message`、`seq` 及接口特有状态。

PowerShell 示例：

```powershell
$body = @{ wxid = "filehelper"; msg = "hello" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:19088/api/sendTextMsg" `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

## 1. 检查微信登录

> **本项目差异：** 当前检查 Weixin.dll 是否已加载，不等同于真实账号登录状态检查；该路径兼容 GET。
### 接口功能
> 检查微信是否登录

### 接口地址
> [/api/checkLogin](/api/checkLogin)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1 成功, 0失败|
|msg|string|成功提示|
|data|object|响应内容|

### 接口示例
入参：
``` javascript
```
响应：
``` javascript
{
    "code": 1,
    "msg": "success",
    "data": {}
}
```

## 2. 获取登录用户信息

> **本项目差异：** 当前 data 不返回 dbKey、dataSavePath、currentDataPath，字段集合以当前 native 账号资料为准。
### 接口功能
> 获取登录用户信息

### 接口地址
> [/api/userInfo](/api/userInfo)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1 成功, 0失败|
|msg|string|成功提示|
|data|object|响应内容|
|&#8194;&#8194;account|string|账号|
|&#8194;&#8194;headImage|string|头像|
|&#8194;&#8194;city|string|城市|
|&#8194;&#8194;country|string|国家|
|&#8194;&#8194;currentDataPath|string|当前数据目录,登录的账号目录|
|&#8194;&#8194;dataSavePath|string|微信保存目录|
|&#8194;&#8194;mobile|string|手机|
|&#8194;&#8194;name|string|昵称|
|&#8194;&#8194;province|string|省|
|&#8194;&#8194;wxid|string|wxid|
|&#8194;&#8194;signature|string|个人签名|
|&#8194;&#8194;dbKey|string|数据库的SQLCipher的加密key，可以使用该key配合decrypt.py解密数据库

### 接口示例
入参：
``` javascript
```
响应：
``` javascript
{
    "code": 1,
    "data": {
        "account": "xxx",
        "city": "Zhengzhou",
        "country": "CN",
        "currentDataPath": "C:\\WeChat Files\\wxid_xxx\\",
        "dataSavePath": "C:\\wechatDir\\WeChat Files\\",
        "dbKey": "965715e30e474da09250cb5aa047e3940ffa1c8f767c4263b132bb512933db49",
        "headImage": "https://wx.qlogo.cn/mmhead/ver_1/MiblV0loY0GILewQ4u2121",
        "mobile": "13949175447",
        "name": "xxx",
        "province": "Henan",
        "signature": "xxx",
        "wxid": "wxid_22222"
    },
    "msg": "success"
}
```

## 3. 发送文本消息
### 接口功能
> 发送文本消息

### 接口地址
> [/api/sendTextMsg](/api/sendTextMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|wxid |true |string| 接收人wxid |
|msg|true |string|消息文本内容|

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,不为0成功, 0失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript
{
    "wxid": "filehelper",
    "msg": "1112222"
}
```
响应：
``` javascript
{"code":345686720,"msg":"success","data":null}
```

## 3.1 发送群聊 @ 文本消息
### 接口功能
> 在群聊中发送带真实 mention 元数据的文本消息。

### 接口地址
> [/api/sendAtText](/api/sendAtText)

### HTTP请求方式
> POST JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|chatRoomId|true|string|目标群聊 wxid|
|wxids|true|string|被 @ 成员的 wxid|
|nickname|true|string|正文中显示的成员昵称|
|msg|true|string|昵称后发送的文本内容|

### 接口示例
```javascript
{
    "chatRoomId": "room-1@chatroom",
    "wxids": "wxid_new",
    "nickname": "新成员",
    "msg": "欢迎加入！请先查看群公告。"
}
```

## 4. hook消息
### 接口功能
> hook接收文本消息，图片消息，群消息.该接口将hook的消息通过tcp回传给本地的端口。
enableHttp=1时，使用url，timeout参数配置服务端的接收地址。请求为post，Content-Type 为json。
enableHttp=0时，使用ip，port的tcp服务回传消息。

### 接口地址
> [/api/hookSyncMsg](/api/hookSyncMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|port |true |string| 本地服务端端口，用来接收消息内容 |
|ip |true |string| 服务端ip地址，用来接收消息内容，可以是任意ip,即tcp客户端连接的服务端的ip|
|url |true |string| http的请求地址，enableHttp=1时，不能为空 |
|timeout |true |string| 超时时间，单位ms|
|enableHttp |true |number| 0/1 ：1.启用http 0.不启用http|

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,0成功, 非0失败|
|data|object|null|
|msg|string|成功提示|


### 接口示例
入参：
``` javascript
{
    "port": "19099",
    "ip":"127.0.0.1",
    "url":"http://localhost:8080",
    "timeout":"3000",
    "enableHttp":"0"
}
```
响应：
``` javascript
{"code":0,"msg":"success","data":null}
```

## 5. 取消hook消息
### 接口功能
> 取消hook消息

### 接口地址
> [/api/unhookSyncMsg](/api/unhookSyncMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,0成功, 非0失败|
|data|object|null|
|msg|string|成功提示|


### 接口示例
入参：
``` javascript

```
响应：
``` javascript
{"code":0,"msg":"success","data":null}
```

## 6. 好友列表

> **本项目差异：** 当前联系人字段与 wxhelper 不完全相同，另支持可选 limit。
### 接口功能
> 好友列表

### 接口地址
> [/api/getContactList](/api/getContactList)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,0成功, 非0失败|
|data|object|好友信息|
|&#8194;&#8194;customAccount|string|自定义账号|
|&#8194;&#8194;encryptName|string|昵称|
|&#8194;&#8194;nickname|string|昵称|
|&#8194;&#8194;pinyin|string|简拼|
|&#8194;&#8194;pinyinAll|string|全拼|
|&#8194;&#8194;reserved1|number|未知|
|&#8194;&#8194;reserved2|number|未知|
|&#8194;&#8194;type|number|未知|
|&#8194;&#8194;verifyFlag|number|未知|
|&#8194;&#8194;wxid|string|wxid|
|msg|string|成功提示|


### 接口示例
入参：
``` javascript

```
响应：
``` javascript
{
    "code": 1,
    "data": [
        {
           "customAccount": "",
            "encryptName": "v3_020b3826fd03010000000000e04128fddf4d90000000501ea9a3dba12f95f6b60a0536a1adb6b40fc4086288f46c0b89e6c4eb8062bb1661b4b6fbab708dc4f89d543d7ade135b2be74c14b9cfe3accef377b9@stranger",
            "nickname": "文件传输助手",
            "pinyin": "WJCSZS",
            "pinyinAll": "wenjianchuanshuzhushou",
            "reserved1": 1,
            "reserved2": 1,
            "type": 3,
            "verifyFlag": 0,
            "wxid": "filehelper"
        }
    ].
    "msg": "success"

```

## 7. 发送文件消息
### 接口功能
> 发送文件消息

### 接口地址
> [/api/sendFileMsg](/api/sendFileMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|wxid |true |string| 接收人wxid |
|filePath|true |string|文件绝对路径|

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,不为0成功, 0失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript
{
    "wxid": "filehelper",
    "filePath": "c:\\test.zip"
}
```
响应：
``` javascript
{"code":345686720,"msg":"success","data":null}
```

## 8. 添加群成员

> **本项目差异：** 当前 memberIds 只支持单个字符串，不支持 JSON 数组。
### 接口功能
> 获取群详情

### 接口地址
> [/api/addMemberToChatRoom](/api/addMemberToChatRoom)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|chatRoomId |true |string| 群id |
|memberIds |true |string| 成员id，多个用,分隔 |

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1成功,负数失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript
{
    "chatRoomId":"12345678901@chatroom",
    "memberIds":"wxid_member_example"
}
```
响应：
``` javascript
{"code":1,"msg":"success","data":null}
```

## 8.1 邀请群成员

> **本项目差异：** 当前 memberIds 只支持单个字符串，不支持 JSON 数组或逗号分隔的多个成员。
### 接口功能
> 邀请成员加入群聊

### 接口地址
> [/api/InviteMemberToChatRoom](/api/InviteMemberToChatRoom)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|chatRoomId|true|string|群id|
|memberIds|true|string|待邀请成员id|
|timeout|false|int|等待原生调用完成的毫秒数，默认 15000|

### 返回字段
|返回字段|字段类型|说明|
|---|---|---|
|code|int|返回状态，1成功，0失败|
|msg|string|成功或失败提示|
|data|object|null|响应内容|

### 接口示例

入参：
``` javascript
{
    "chatRoomId":"12345678901@chatroom",
    "memberIds":"wxid_member_example",
    "timeout":15000
}
```
响应：
``` javascript
{"code":1,"msg":"success","data":null}
```

## 8.2 创建群聊

> **本项目差异：** `memberIds` 同时支持 JSON 字符串数组和逗号分隔字符串；群名称可通过 `name`、`topic` 或 `chatRoomName` 传入。成员去重后必须有 2 至 40 个，每个 ID 最长 255 字节；空项或包含 `@chatroom` 的 ID 会被拒绝。

### 接口功能
> 创建群聊并返回新群的 `roomid`

### 接口地址
> [/api/createChatRoom](/api/createChatRoom)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|memberIds|true|array/string|初始成员 wxid；可传 JSON 字符串数组或逗号分隔字符串；有效唯一成员须为 2 至 40 个，单个 ID 最长 255 字节|
|name / topic / chatRoomName|false|string|群名称，三个字段为兼容别名|
|timeout|false|int|等待原生调用完成的毫秒数，默认 15000|

### 返回字段
|返回字段|字段类型|说明|
|---|---|---|
|code|int|返回状态，1成功，0失败|
|msg|string|成功或失败提示|
|data|object|原生创建结果；成功时包含带 `@chatroom` 的新群 `roomid`|
|data.memberIds|string|规范化后的逗号分隔成员 wxid|
|data.memberCount|int|参与创建的成员数量|
|data.topic|string|实际提交的群名称|
|data.command|string|固定为 `create_chatroom`|
|data.path|string|固定为 `native_chatroom_create`|
|data.cgiType|int|固定为 `119`|
|data.endpoint|string|固定为 `/cgi-bin/micromsg-bin/createchatroom`|
|data.upperCall / data.upperCallOffset|string|已验证的 4.1.9.23 上层调用及偏移|
|data.waitStatus / data.queueStatus|int|等待与微信发送线程入队状态|
|data.dispatchResult / data.dispatchResultKind|string|高层函数返回的 `out_roomid` 指针及其含义，仅供诊断，不是业务状态|
|data.service / data.phase|string/int|业务服务对象和执行阶段诊断|
|data.sehCode / data.sehAddr|int/string|原生异常诊断；正常调用时 `sehCode` 为 0|

解析 `memberIds` 时会拒绝空项和包含 `@chatroom` 的成员 ID，并在去重后检查 2 至 40 个成员的数量限制。底层调用使用 CGI 类型 `119` 和 endpoint `/cgi-bin/micromsg-bin/createchatroom`。`data` 保留完整原生诊断，调用方应以 `code=1` 且 `data.roomid` 包含 `@chatroom` 作为创建成功依据。

### 接口示例

入参（JSON 数组）：
``` javascript
{
    "memberIds":["wxid_member_a","wxid_member_b"],
    "name":"项目讨论群",
    "timeout":15000
}
```

`memberIds` 也可写为：
``` javascript
{
    "memberIds":"wxid_member_a,wxid_member_b",
    "topic":"项目讨论群"
}
```

响应：
``` javascript
{
    "code":1,
    "msg":"success",
    "data":{
        "ok":true,
        "command":"create_chatroom",
        "path":"native_chatroom_create",
        "roomid":"12345678901@chatroom",
        "memberIds":"wxid_member_a,wxid_member_b",
        "memberCount":2,
        "topic":"项目讨论群",
        "cgiType":119,
        "endpoint":"/cgi-bin/micromsg-bin/createchatroom",
        "upperCall":"sub_181544030",
        "upperCallOffset":"0x1544030",
        "waitStatus":0,
        "queueStatus":1,
        "sehCode":0
    }
}
```

## 9. 修改群昵称

> **本项目差异：** 当前只修改本账号群昵称，不读取 wxhelper 的 wxid。
### 接口功能
> 修改群昵称

### 接口地址
> [/api/modifyNickname](/api/modifyNickname)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|chatRoomId |true |string| 群id |
|wxid |true |string| 自己的wxid |
|nickName |true |string| 昵称 |


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1成功, -1失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript

{
    "chatRoomId":"31004@chatroom",
    "wxid":"wxid_example",
    "nickName":"1221"
}
```
响应：
``` javascript
{"code":1,"msg":"success","data":null}
```

## 10. 删除群成员

> **本项目差异：** 当前 memberIds 只支持单个字符串，不支持 JSON 数组。
### 接口功能
> 删除群成员

### 接口地址
> [/api/delMemberFromChatRoom](/api/delMemberFromChatRoom)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|chatRoomId |true |string| 群id |
|memberIds |true |string| 成员id,用,分隔 |



### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1成功, -1失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript

{
    "chatRoomId":"1234@chatroom",
    "memberIds":"wxid_123"
}
```
响应：
``` javascript
{"code":1,"msg":"success","data":null}
```

## 11. 获取群成员

> **本项目差异：** data 使用当前 native 群成员结果结构，不保证与 wxhelper 完全一致。
### 接口功能
> 获取群成员

### 接口地址
> [/api/getMemberFromChatRoom](/api/getMemberFromChatRoom)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|chatRoomId |true |string| 群id |



### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1成功, -1失败|
|msg|string|成功提示|
|data|object|null|
|&#8194;&#8194;chatRoomId|string|群id|
|&#8194;&#8194;members|array|成员列表|
|&#8194;&#8194;&#8194;&#8194;wxid|string|成员id|
|&#8194;&#8194;&#8194;&#8194;Nickname|string|成员昵称|
|&#8194;&#8194;number|int|成员数量|
|&#8194;&#8194;admin|string|群管理|
|&#8194;&#8194;adminNickname|string|管理昵称|

### 接口示例

入参：
``` javascript

{
    "chatRoomId":"1234@chatroom"
}
```
响应：
``` javascript
{
    "code": 1,
    "data": {
        "admin": "wxid_2222",
        "adminNickname": "123",
        "chatRoomId": "22224@chatroom",
        "members": [
            {
                "wxid": "wxid_2222",
                "Nickname": "123"
            },
            {
                "wxid": "wxid_333",
                "Nickname": ""
            }
        ],
        "number": 2
    },
    "msg": "success"
}
```

## 12. 退群
### 接口功能
> 退群

### 接口地址
> [/api/quitChatRoom](/api/quitChatRoom)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|chatRoomId|string|群id|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript
{
    "chatRoomId":"123456@chatroom"
}

```
响应：
``` javascript
{
    "code": 119536579329,
    "data": null,
    "msg": "success"
}
```

## 13. 转发消息

> **本项目差异：** 当前不查询消息数据库，仅传 msgId 时依赖运行时缓存；历史消息可能需要补充原消息元数据。
### 接口功能
> 转发消息

### 接口地址
> [/api/forwardMsg](/api/forwardMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|string|接收人id|
|msgId|string|消息id|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1成功, -1失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript
{
    "wxid":"filehelper",
    "msgId":"1233312233123"
}

```
响应：
``` javascript
{
    "code": 1,
    "data": null,
    "msg": "success"
}
```

## 14. 朋友圈首页

> **本项目差异：** 当前是 getSnsTimeline 的兼容别名。
### 接口功能
> 朋友圈首页,前置条件需先调用hook消息接口成功,具体内容会在hook消息里返回，格式如下：
``` javascript
{
    "data":[
        {
            "content": "",
            "createTime': 1691125287,
            "senderId': "",
            "snsId': 123,
            "xml':""
        }
    ]
}
```

### 接口地址
> [/api/getSNSFirstPage](/api/getSNSFirstPage)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1成功, -1失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript


```
响应：
``` javascript
{
    "code": 1,
    "data": null,
    "msg": "success"
}
```

## 15. 朋友圈下一页

> **本项目差异：** 当前忽略 snsId，仍调用 timeline，不保证真实下一页语义。
### 接口功能
> 朋友圈下一页

### 接口地址
> [/api/getSNSNextPage](/api/getSNSNextPage)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|snsId|number|snsId|



### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,1成功, -1失败|
|msg|string|成功提示|
|data|object|null|


### 接口示例

入参：
``` javascript
{

    "snsid":123
}

```
响应：
``` javascript
{
    "code": 1,
    "data": null,
    "msg": "success"
}
```

## 16. 获取群成员信息

> **本项目差异：** data 是当前联系人扫描结果中的单个对象，支持可选 limit。
### 接口功能
> 获取群成员基础信息

### 接口地址
> [/api/getContactProfile](/api/getContactProfile)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|string|wxid|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|
|&#8194;&#8194;account|string|账号|
|&#8194;&#8194;headImage|string|头像|
|&#8194;&#8194;nickname|string|昵称|
|&#8194;&#8194;v3|string|v3|
|&#8194;&#8194;wxid|string|wxid|

### 接口示例

入参：
``` javascript

{
    "wxid":"wxid_123"
}

```
响应：
``` javascript
{
    "code": 1,
    "data": {
        "account": "account",
        "headImage": "https://wx.qlogo.cn/mmhead/ver_1/0",
        "nickname": "test",
        "v3": "wxid_123",
        "wxid": "wxid_123"
    },
    "msg": "success"
}
```

## 17. 发送公众号消息
### 接口功能
> 自定义发送公众号消息

### 接口地址
> [/api/forwardPublicMsg](/api/forwardPublicMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|appName|string|公众号id，消息内容里的appname|
|userName|string|公众号昵称，消息内容里的username|
|title|string|链接地址，消息内容里的title|
|url|string|链接地址，消息内容里的url|
|thumbUrl|string|缩略图地址，消息内容里的thumburl|
|digest|string|摘要，消息内容里的digest|
|wxid|string|wxid|

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript

{
    "appName": "快手",
    "userName": "gh_271633",
    "title": "PC硬件、数码产品彻底反转",
    "url": "http://mp.weixin.qq.com/s?__biz=Mzg3MzYg==&mid=22440&idx=1&sn=bd8e8b0d9f2753f3c340&chksm=ced16f2ff9a6e639cc9bb76631ff03487f86486f0f29fcf9f8bed754354cb20eda31cc894a56&scene=0&xtrack=1#rd",
    "thumbUrl": "https://mmbiz.qpic.cn/sz__jpg/tpzwaqMCicQyEkpxmpmmP9KgoBHiciamYhqZ0ff4kNlozxgRq4AtEzibo4iaw/640?wxtype=jpeg&wxfrom=0",
    "digest": "这谁顶得住？",
    "wxid": "filehelper"
}

```
响应：
``` javascript
{
    "code": 1,
    "data": {},
    "msg": "success"
}
```

## 18. 转发公众号消息

> **本项目差异：** 当前依赖运行时消息缓存；历史消息可能需要补充原消息元数据。
### 接口功能
> 转发公众号消息

### 接口地址
> [/api/forwardPublicMsgByMsgId](/api/forwardPublicMsgByMsgId)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|msgId|number|msgId|
|wxid|string|wxid|

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript

{
   "msgId": 8871595889497690337,
    "wxid": "filehelper"
}

```
响应：
``` javascript
{
    "code": 1,
    "data": {},
    "msg": "success"
}
```

## 19. 下载附件

> **本项目差异：** 当前仅支持图片任务；sourceType 缺省、0 或 3 时尝试下载，其他类型返回失败。
### 接口功能
> 下载附件，保存在微信文件目录下  wxid_xxx/wxhelper 目录下

### 接口地址
> [/api/downloadAttach](/api/downloadAttach)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|msgId|number|msgId|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript

{
   "msgId": 887159588949767
}

```
响应：
``` javascript
{
    "code": 1,
    "data": {},
    "msg": "success"
}
```

## 20. 解码图片
### 接口功能
> 解码图片

### 接口地址
> [/api/decodeImage](/api/decodeImage)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|filePath|string|待解码图片地址|
|storeDir|string|解码后图片的存储目录|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript

{
   "filePath": "C:\\886206666148161980131.dat",
   "storeDir":"C:\\test"
}

```
响应：
``` javascript
{
    "code": 1,
    "data": {},
    "msg": "success"
}
```

## 21. 发送图片
### 接口功能
> 发送图片

### 接口地址
> [/api/sendImagesMsg](/api/sendImagesMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|string|wxid|
|imagePath|string|图片路径|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript

{
    "wxid":"filehelper",
    "imagePath":"C:\\test.png"
}

```
响应：
``` javascript
{
    "code": 1,
    "data": {},
    "msg": "success"
}
```

## 22. 发送自定义表情
### 接口功能
> 发送自定义表情

### 接口地址
> [/api/sendCustomEmotion](/api/sendCustomEmotion)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|string|wxid|
|filePath|string|表情路径，可以直接查询CustomEmotion表的MD5字段,路径规则见下面示例|


### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态,大于0成功, -1失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript

{
    "wxid":"filehelper",
    "filePath":"C:\\wechatDir\\WeChat Files\\wxid_123\\FileStorage\\CustomEmotion\\8F\\8F6423BC2E69188DCAC797E279C81DE9"
}

```
响应：
``` javascript
{
    "code": 1,
    "data": {},
    "msg": "success"
}
```

## 23. 发送XML消息

> **本项目差异：** 当前不读取 path，省略 type 时默认值为 0 而不是 33。
### 接口功能
> 发送XML自定义消息

### 接口地址
> [/api/sendXmlMsg](/api/sendXmlMsg)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|true|string|接收人的wxid|
|xml|true|string|XML文本内容|
|path|false|string|图片等附件路径(可选)|
|type|false|int|XML消息类型(默认33)|

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态, 1成功, 0失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript
{
    "wxid":"wxid_1234",
    "xml":"<msg><appmsg appid=\"\" sdkver=\"0\"><title>test</title><des>test content</des><type>33</type></appmsg></msg>",
    "path":"",
    "type":33
}
```
响应：
``` javascript
{
    "code": 1,
    "data": null,
    "msg": "success"
}
```

## 24. 信息防撤回
### 接口功能
> 开启/关闭防止对方撤回消息。开启后，对方触发撤回时，我方的消息不会被删除。

### 接口地址
> [/api/antiRevoke](/api/antiRevoke)

### HTTP请求方式
> POST  JSON

### 请求参数
|参数|必选|类型|说明|
|---|---|---|---|
|enable|true|int|1开启防撤回，0关闭防撤回|

### 返回字段
|返回字段|字段类型|说明                              |
|---|---|---|
|code|int|返回状态, 0成功, 非0失败|
|msg|string|成功提示|
|data|object|null|

### 接口示例

入参：
``` javascript
{
    "enable": 1
}
```
响应：
``` javascript
{
    "code": 0,
    "data": {},
    "msg": "success"
}
```

## 4.1.9.23 接口适配说明

本版本保持 wxhelper 原有 HTTP 风格：所有接口仍为 `POST JSON`，返回结构仍为 `code/data/msg`。

HTTP 路由直接调用具名 Manager 方法，Manager 再直接调用对应的 4.1.9.23 内部偏移实现。这里的内部偏移实现是 DLL 内部 wrapper，不是调用方可见协议。不存在 outbox/result/inbox 文件命令传输，也不存在 `type + vector<string>` 或 `[[wx_*]]` 文本协议。异步调用仅通过进程内结果通道关联返回。接收消息只通过已配置的 HTTP/TCP 回调推送，不再降级写入 inbox 文件。worker 生命周期由 wxhelper 直接管理，不再使用 startup/owner/stop 文件握手。日志、待发送媒体路径和下载目标路径仍是普通业务文件，不属于命令传输协议。

未列为 4.1.9.23 新增接口的旧 `/api`，调用方继续使用原接口地址、请求参数和返回结构；本版本只替换这些接口的内部实现。

本版本已适配 4.1.9.23 已验证偏移和实现的能力，包括：文本/图片/文件/表情发送、引用消息、appmsg XML/卡片、消息转发、接收消息 hook、防撤回、好友申请接受/自动接受、创建群聊、群成员读取、群成员增加/邀请/删除、修改本人群昵称、修改群名、发布群公告、退出群、A8Key、H5ExtTransfer、Finder 搜索/直播间进入/直播发言/用户页、打开 URL、朋友圈 timeline/发布/评论/点赞/删除/可见性、SNS 媒体下载、收款转账接受/拒绝、语音转文字。

以下旧接口在 4.1.9.23 参考实现中没有可直接验证的偏移，本版本未把旧 3.9.5.81 偏移作为新版本依据：收藏、拍一拍、小程序、OCR、置顶/取消置顶、公众号文章转发、联系人详情读取等。保留原有接口声明，实际稳定性以后续 4.1.9.23 偏移补齐为准。

## 4.1.9.23 新增接口

## 25. 发送引用消息
接口地址：`/api/sendQuoteMsg`

请求参数：
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|true|string|接收人 wxid|
|msg|true|string|回复正文|
|quotedMsgId|true|string/number|被引用消息 MsgSvrID|
|quotedLocalId|true|string/number|被引用消息 localId|
|quotedContent|true|string|被引用消息内容|
|quotedFrom|true|string|被引用消息发送者 wxid|
|quotedType|true|number|被引用消息类型，文本通常为 1|
|createTime|true|number|被引用消息时间戳|
|quotedChat|false|string|被引用消息所在会话|
|msgSource|false|string|被引用消息 msgsource|
|sourceToWxid|false|string|源消息 to/self|

## 25b. 发送「图片引用文字」消息
接口地址：`/api/sendImageQuoteMsg`

发送一张图片，并让**图片消息本身**携带对某条真实消息的原生引用关联（不是"引用文字＋普通图片"两条消息）。

微信 4.1.9.23 的图片引用与 appmsg 引用走**同一个** optional send_item 子对象（`inner+0x208` + presence `inner+0x590`），
区别只有两点：外层消息类型是 3（图片）而非 49/57；序列化时 refermsg 落在 `<extcommoninfo>` 而不是 `<appmsg>`，且只输出两个字段：

```xml
<msg><img .../><commenturl/><extcommoninfo><refermsg>
  <createtime>源消息 create_time</createtime><svrid>源消息 server_id</svrid>
</refermsg></extcommoninfo></msg>
```

对照：普通图片 XML 到 `<commenturl/>` 即结束，不含 `<extcommoninfo>`。两个值必须取自被引消息**真实的行**，桥不代填。

请求参数：
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|true|string|接收人 wxid|
|imagePath|true|string|图片路径；支持中文路径|
|quotedMsgId|true|string/number|被引用消息 MsgSvrID；**建议传十进制字符串**避免 JS 精度丢失|
|quotedLocalId|true|string/number|被引用消息 localId|
|createTime|true|number|被引用消息时间戳（须与 quotedMsgId 来自同一行）|
|quotedFrom|true|string|被引用消息发送者 wxid|
|quotedType|true|number|被引用消息类型，引用文字固定为 1|
|quotedContent|false|string|被引用消息内容|
|quotedChat|false|string|被引用消息所在会话；缺省回退 wxid|
|msgSource|false|string|被引用消息 msgsource|
|sourceToWxid|false|string|源消息 to/self|

响应：
```json
{"code":1,"msg":"accepted","data":{
  "accepted":1,"messageId":null,"stage":"bridge-accepted",
  "note":"handed to WeChat send path; this path does not return a message id"}}
```

**阶段语义**（不要把 `code=1` 当功能验收）：
- `code=-1` + `msg` 以 `validation:` 开头 —— 参数校验失败，**没有发送任何消息**
- `code=-1` + `id.send-rejected` —— 微信发送路径拒绝，**没有发送任何消息，不会退化成普通图片**
- `code=1` + `stage=bridge-accepted` —— 仅表示桥已把消息交给微信发送路径

`messageId` 恒为 `null`：该发送路径不回传微信消息 ID。要确认是否真的发出，请查库核对新增行的
`local_type`（应为 3）与 `<extcommoninfo><refermsg>` 中的 `svrid`/`createtime` 是否等于源消息。

### 中文路径与 `\uXXXX`
桥依赖的 mongoose `json_unescape` 只解 `\u00XX`，遇到中文等字符直接放弃。Python 的 `json.dumps`
默认 `ensure_ascii=True`（即 `\uXXXX`），会因此把中文路径静默变成空串。
`http_api.c` 的 `json_str` 已加一层 `\uXXXX` 解码回退（支持 BMP 与代理对），
所以 `\uXXXX` 转义与裸 UTF-8 **两种写法都可用**；未修改 `third_party/mongoose.c`。

## 26. 发送卡片消息
接口地址：`/api/sendCardMsg`

请求参数：
|参数|必选|类型|说明|
|---|---|---|---|
|wxid|true|string|接收人 wxid|
|title|true|string|标题|
|digest|false|string|摘要|
|subtype|true|number|appmsg subtype|
|url|false|string|跳转 URL|
|thumbUrl|false|string|封面 URL|
|appid|false|string|appid|
|sourceUsername|false|string|来源 username|
|sourceDisplayName|false|string|来源显示名|

## 27. A8Key / H5 / Finder

|接口地址|说明|主要参数|
|---|---|---|
|`/api/getA8Key`|获取 A8Key|`url`, `scene`, `urlType`, `timeout`|
|`/api/h5ExtTransfer`|H5ExtTransfer|`url`, `cmdId`, `reqData`, `jsapiArgs`, `timeout`, `jsapiControlByte`, `sessionId`, `reportSecurity`, `transferCgiUrl`|
|`/api/searchFinder`|搜索视频号、视频和直播|`query`, `offset`, `scene`, `businessType`, `subType`, `currentPage`, `sessionId`, `requestId`, `timeout`|
|`/api/enterFinderLive`|进入 Finder 直播间|`liveUrl`，或 `objectId` + `objectNonceId`; 兼容旧名 `url` / `nonceId`|
|`/api/sendFinderLiveComment`|直播间发言|`content`, `liveId`, `objectId`; 会话字段缺失时自动 Init/Join|
|`/api/postFinderLiveComment`|直播间发言别名|参数同 `/api/sendFinderLiveComment`|
|`/api/getFinderUserPage`|Finder 用户页|`username`, `objectId`, `contextId`, `entranceId`, `finderUsername`, `h5Url`, `scene`, `timeout`|

`/api/h5ExtTransfer` 的完整响应位于返回 `data.response`，不再通过 `responseFile` sidecar 文件传递。`jsapiControlByte` 默认 `400`；request `+0xA8` 是由 H5 manager 配置覆盖的 `pub_key_token`，不是调用方可传入的 `h5AuthToken`。

### Finder 搜索

`/api/searchFinder` 使用微信搜索 H5 的 `mmpcwebsearch` 通道。`query` 必填。首次请求可省略 `currentPage`，此时使用 `1`；当 `offset > 0` 时必须显式传入 `currentPage`，避免把结果偏移误当页码。返回的 `data.response` 是原始搜索响应，可用 `python/message/finder_payload.py` 提取 `objectId`、`objectNonceId`、`liveId`、`contextId`、`username` 和直播页 URL。`data.transportOk` 表示 H5 传输完成，`data.businessOk` 同时检查外层 `jsapi_resp.ret` 和内层 `BaseResponse.Ret`；只有两者都成功时 `data.ok=true`。

Finder 的 64 位 ID 应作为 JSON 字符串传入，避免 JavaScript 数值精度损失。直播搜索结果的 `objectId` 也可能是 `export/...`；它实际是 encrypted/export token，进房时走 `eid`，发言前由详情 CGI 解析成数字对象 ID，不能直接提交给 JoinLive。

### 进入直播间

`/api/enterFinderLive` 支持直接传入完整 `liveUrl`，也支持由 `objectId` 和 `objectNonceId` 构造。`python/message/finder_payload.py` 生成的 `liveUrl` 可原样提交；`muted=true` 可静音打开。`export/...` 自动生成 `eid=...` 路由，此时 `objectNonceId` 可省略；普通数字 ID 则按官方 `Base64URL(uint64 big-endian)` 规则生成 `oid/nid`。接口同时兼容 `url`、`nonceId` 以及 context/entrance 参数的 snake_case 旧名：

```json
{
  "objectId": "export/UzFfBgAA...",
  "contextId": "live-context",
  "entranceId": "88",
  "muted": true
}
```

接口会打开 `https://channels.weixin.qq.com/web/pages/live` 真实直播页，并在 `data.url` 返回最终 URL。

### 直播发言

`/api/sendFinderLiveComment` 按官方 PC 直播页结构调用 `/cgi-bin/micromsg-bin/pc_finderpostlivemsg`（cmd `4145`，scope `finderLive`，scene `142`）。请求示例：

```json
{
  "content": "测试评论",
  "liveId": "9988776655",
  "objectId": "14929828060974024712",
  "objectNonceId": "live-nonce",
  "contextId": "live-context",
  "entranceId": "88"
}
```

当 `objectId` 为 `export/...`，或传入的 nonce 尚不是 Join 专用格式时，桥先调用 `pc_findergetcommentdetail`（cmd `5259`），从 `object.id`、`object.object_nonce_id` 和 `object.live_info.live_id` 取得规范值。若未提供 `userverForH5`，随后调用 `pc_finderinit`（cmd `5831`），并从响应提取 `myacct[0].username` 和 `currentAliasRoleType`。role `3` 使用当前 Finder 账号 username；role `1` 且 `myacct=[]` 时按官方前端行为省略 Join/Post 的 `finder_username`，不能回退到 `wx_username_for_h5`。JoinLive（cmd `4233`）成功后再调用 `pc_finderpostlivemsg`（cmd `4145`）。各调用使用同一直播页 URL、scope `finderLive`、scene `142`、`jsapiControlByte=400` 和 `reportSecurity=false`。首次 Init 不携带 `liveIdentity`。

|参数|必选|类型|说明|
|---|---|---|---|
|`content`|true|string|评论正文|
|`liveId`|true|string/number|直播 ID，建议字符串|
|`objectId`|true|string/number|十进制对象 ID，或搜索返回的 `export/...` encrypted token；建议始终传字符串|
|`liveCookies` / `live_cookies`|false|string|JoinLive 返回值；省略时自动 JoinLive|
|`objectNonceId` / `nonceId`|条件|string|数字对象 ID 进房时必填；`export/...` 可自动通过详情 CGI 取得规范 nonce|
|`liveUrl` / `h5Url` / `url`|条件|string|完整直播页 URL；未传时由 object/nonce/context 参数构造|
|`identityUsername`|false|string|当前 Finder 身份 username 的显式覆盖；一般省略并由 Init 的 `myacct[0].username` 自动提取，不能使用主播 username 或 `wx_username_for_h5`|
|`senderFinderUsername` / `commentFinderUsername`|false|string|`identityUsername` 的别名|
|`finderUsername` + `useFinderIdentity=true`|false|string|显式确认后兼容旧字段；搜索结果中的该字段通常是主播账号，不能直接当发言身份|
|`clientMsgId`|false|string|客户端消息 ID；role `3` 默认 `pc_<Finder username>_<唯一后缀>`，role `1` 且无 `myacct` 时按官方行为生成 `pc_undefined_<唯一后缀>`|
|`contextId`|false|string|搜索或进房上下文 ID|
|`entranceId`|false|string|入口 ID，默认 `88`|
|`subEntranceId`|false|string|子入口 ID|
|`liveIdentity`|false|number|`1` 微信、`2` alias、`3` Finder、`4` undefined；显式值优先，否则自动使用 Init 返回的当前 role|
|`userverForH5` / `userver_for_h5`|false|任意 JSON 值|Finder Init 返回值；省略时自动 Init，保留原始 JSON 类型|
|`timeout`|false|number|等待毫秒数，默认 `15000`|

评论必须非空且最多 60 个 Unicode 计数单位，换行和制表符在发送前转为空格。返回的 `data.stage` 表示失败或完成阶段，其中 `resolve` 表示 encrypted ID/nonce 解析；`initOk`、`joinOk`、`postOk` 给出后续阶段状态。成功时 `data.objectId` 和 `data.liveId` 是详情解析后的数字值，`data.session.userverForH5` 与 `data.session.liveCookies` 可复用于后续评论。

### 获取主页

`/api/getFinderUserPage` 默认调用当前 Web Finder 页面使用的 `pc_finderuserpage`（H5ExtTransfer cmd `6624`、scope `finderLive`、默认 scene `140`），不再把旧的 `finderuserpagepreview`（CGI `6611`）错误码包装成成功。`username` 必填，通常直接使用搜索结果中的 Finder `username`；`objectId` 可选，`contextId`/`entranceId`/`h5Url` 可复用搜索或页面事件中的值。`finderUsername` 仅作为请求中的可选当前 Finder 身份字段；当未传 `username` 时它可作为目标别名，但不会同时作为身份发送。

稳定结果位于 `data.homepage`：`profile` 为官方 `contact`（含 `nickname`、`head_url`、`signature` 等）并带 `parsed=true`，`feeds` 为官方 `object` 数组，另有 `feedsCount`、`cursor`、`hasMore`、`userTags`、`transportOk`、`lowerTransportOk`、`outerTransportOk` 和 `dataAvailable`。`data.response` 保留 H5 内层原始 JSON，`data.responseFile` 保留 sidecar 路径；顶层 `ok` 只有在 H5 传输、外层/内层业务返回成功且至少解析出 profile 或一条 feed 时才为 true。仅需复现旧 6611 model 时可显式传 `useLegacyModel=true`。

## 28. 联系人/群资料修改

|接口地址|说明|主要参数|
|---|---|---|
|`/api/modifyRemark`|修改联系人备注|`wxid`, `remark`|
|`/api/modifyChatRoomName`|修改群名|`chatRoomId`, `name`|
|`/api/publishChatRoomAnnouncement`|发布群公告|`chatRoomId`, `announcement`|
|`/api/openUrl`|在微信内打开 URL|`url`|

## 29. 朋友圈

> **本项目差异：** 当前未完整实现 locationXml、poiName 语义，支持 md5、cdnUrl、thumbUrl/thumbPath。 当前未完整实现 locationXml 语义，支持 md5、thumbUrl/thumbPath。

|接口地址|说明|主要参数|
|---|---|---|
|`/api/getSnsTimeline`|拉取朋友圈 timeline|`timeout`, `richMode`|
|`/api/publishSnsText`|发布纯文本朋友圈|`content`, `timeout`|
|`/api/publishSnsImage`|发布图片朋友圈|`content`, `imagePath`, `thumbPath`, `width`, `height`, `timeout`, `locationXml`, `poiName`|
|`/api/publishSnsVideo`|发布视频朋友圈|`content`, `videoPath`, `thumbPath`, `width`, `height`, `timeout`, `locationXml`, `durationMs`|
|`/api/snsComment`|评论朋友圈|`snsId`, `username`, `content`, `clientId`, `objectNonceId`, `parentId`, `replyCommentId`, `replySource`, `replyType`, `replyCreateTime`, `timeout`|
|`/api/snsLike`|点赞朋友圈|`snsId`, `username`, `objectNonceId`, `timeout`|
|`/api/snsDelete`|删除朋友圈|`snsId`, `username`, `objectNonceId`, `timeout`|
|`/api/snsSetVisibility`|设置朋友圈可见性|`snsId`, `username`, `objectNonceId`, `clientId`, `objectXml`, `visibilityState`, `timeout`|

`visibilityState` 取值沿用 4.1.9.23 native 实现：`1` 为公开，`2` 为仅自己可见。

## 30. SNS 媒体下载

|接口地址|说明|主要参数|
|---|---|---|
|`/api/downloadSnsMediaUrl`|按 URL 下载 SNS 媒体|`url`, `savePath`, `timeout`|
|`/api/downloadSnsMediaTask`|按 SNS 媒体任务下载|`snsId`, `mediaId`, `url`, `savePath`, `totalSize`, `timeout`|
|`/api/downloadSnsMediaFullTask`|完整 SNS 媒体任务下载|`snsId`, `mediaId`, `url`, `thumbUrl`, `savePath`, `totalSize`, `taskType`, `timeout`|

## 31. 转账和语音

> **本项目差异：** 返回语音转文字任务结果，不等同于 getVoiceByMsgId 音频文件导出。

|接口地址|说明|主要参数|
|---|---|---|
|`/api/acceptTransfer`|接受转账|`transferId`, `transactionId`, `invalidTime`, `sender`|
|`/api/refuseTransfer`|拒绝转账|`transferId`, `invalidTime`, `sender`|
|`/api/voiceTranscribe`|语音转文字|`msgId`, `localId`, `activeSubmit`|

## 32. 接受好友申请

自动接受默认关闭。启用后，接收 hook 会识别消息类型 `37`，从 `<msg>` 属性读取加密用户名、ticket 和 scene，并在线程池中串行调用微信原生接受流程。

|接口地址|说明|主要参数|
|---|---|---|
|`/api/acceptFriend`|手动接受好友申请|`xml`，或 `encryptUsername`/`v3`、`ticket`/`v4`、`scene`；可选 `friendFlag`, `timeout`|
|`/api/autoAcceptFriend`|开启、关闭或查询自动接受|可选 `enable`；省略时仅返回当前状态|

`/api/acceptFriend` 兼容 `encryptusername`、`wxid`、`antiSpamTicket`、`antispamticket`、`friendflag` 字段别名。返回 `data` 包含 `ok`、`errorCode`、`opcode`、`exceptionCode` 和 `message`。

开启自动模式：

```json
{"enable": true}
```

自动模式响应中的 `queued`、`succeeded`、`failed`、`parseFailed`、`deduplicated` 是当前 DLL 生命周期内的累计计数。

## 当前未提供的 wxhelper API

- 数据库：`/api/getDBInfo`、`/api/execSql`（尚无 native 方法，暂不实现）。
- 群扩展：`/api/getChatRoomDetailInfo`。
- 消息扩展：`/api/topMsg`、`/api/removeTopMsg`、`/api/sendPatMsg`。
- 收藏：`/api/addFavFromMsg`、`/api/addFavFromImage`。
- 其他：`/api/hookLog`、`/api/unhookLog`、`/api/getVoiceByMsgId`、`/api/sendApplet`、`/api/ocr`、`/api/test`。
