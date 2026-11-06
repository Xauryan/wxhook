import { Fragment } from 'react'

// The classic WeChat emoticons are bundled, so chat text never requires a CDN.
const names =
  '微笑 撇嘴 色 发呆 得意 流泪 害羞 闭嘴 睡 大哭 尴尬 发怒 调皮 呲牙 惊讶 难过 酷 冷汗 抓狂 吐 偷笑 可爱 白眼 傲慢 饥饿 困 惊恐 流汗 憨笑 大兵 奋斗 咒骂 疑问 嘘 晕 折磨 衰 骷髅 敲打 再见 擦汗 抠鼻 鼓掌 糗大了 坏笑 左哼哼 右哼哼 哈欠 鄙视 委屈 快哭了 阴险 亲亲 吓 可怜 菜刀 西瓜 啤酒 篮球 乒乓 咖啡 饭 猪头 玫瑰 凋谢 示爱 爱心 心碎 蛋糕 闪电 炸弹 刀 足球 瓢虫 便便 月亮 太阳 礼物 拥抱 强 弱 握手 胜利 抱拳 勾引 拳头 差劲 爱你 NO OK 爱情 飞吻 跳跳 发抖 怄火 转圈 磕头 回头 跳绳 挥手 激动 街舞 献吻 左太极 右太极'.split(
    ' '
  )
const classic = new Map(names.map((name, index) => [name, index]))
const aliases: Record<string, string> = {
  Smile: '微笑',
  Grin: '呲牙',
  Chuckle: '憨笑',
  Laugh: '偷笑',
  Sob: '大哭',
  Cry: '流泪',
  Blush: '害羞',
  Tongue: '调皮',
  ThumbsUp: '强',
  Shake: '握手',
  Salute: '抱拳',
  Joyful: '憨笑',
  Heart: '爱心',
  Rose: '玫瑰',
  Sleep: '睡',
  Bye: '再见',
  爱情: '爱情',
  发抖: '发抖',
  糗大了: '糗大了',
  嘴唇: '示爱',
}
// Newer expressions have no equivalent in the bundled classic artwork.
const modern: Record<string, string> = {
  破涕为笑: '😂',
  笑脸: '😄',
  嘿哈: '😄',
  捂脸: '🤦',
  奸笑: '😏',
  机智: '🤓',
  皱眉: '😟',
  耶: '✌️',
  吃瓜: '🍉',
  加油: '💪',
  汗: '😓',
  天啊: '😱',
  Emm: '😐',
  社会社会: '🙏',
  旺柴: '🐶',
  好的: '👌',
  打脸: '🤦',
  哇: '🤩',
  翻白眼: '🙄',
  '666': '🤙',
  让我看看: '🧐',
  叹气: '😮‍💨',
  苦涩: '🥲',
  裂开: '🫠',
  合十: '🙏',
  庆祝: '🎉',
  烟花: '🎆',
  爆竹: '🧨',
  红包: '🧧',
  福: '🧧',
  發: '🀅',
  发: '🀅',
  转圈圈: '💫',
  炸弹: '💣',
  Fireworks: '🎆',
  Party: '🎉',
  Facepalm: '🤦',
  Lol: '😂',
}

export function WechatText({ text = '' }: { text?: string }) {
  return text.split(/(\[[^\[\]\n]{1,24}\])/g).map((part, index) => {
    const token =
      part.startsWith('[') && part.endsWith(']') ? part.slice(1, -1) : ''
    const name = aliases[token] || token
    const sprite = classic.get(name)
    if (sprite !== undefined)
      return (
        <img
          key={index}
          className='wechat-emoticon'
          src={`/images/wechat-emoticons/${sprite}.gif`}
          alt={part}
          title={part}
          draggable={false}
        />
      )
    if (modern[name])
      return (
        <span
          key={index}
          className='wechat-emoticon wechat-emoticon-native'
          role='img'
          aria-label={part}
          title={part}
        >
          {modern[name]}
        </span>
      )
    return <Fragment key={index}>{part}</Fragment>
  })
}
