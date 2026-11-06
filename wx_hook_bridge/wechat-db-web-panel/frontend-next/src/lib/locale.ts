import { z } from 'zod'

z.config(z.locales.zhCN())

export const UI_LOCALE = 'zh-CN'

const labels: Record<string, string> = {
  id: '编号',
  username: '用户名',
  fullName: '姓名',
  firstName: '名',
  lastName: '姓',
  email: '邮箱',
  phoneNumber: '手机号',
  role: '角色',
  status: '状态',
  title: '标题',
  label: '标签',
  priority: '优先级',
  createdAt: '创建时间',
  updatedAt: '更新时间',
  dueDate: '截止日期',
  assignee: '负责人',
  description: '说明',
  active: '已启用',
  inactive: '已停用',
  invited: '待接受邀请',
  suspended: '已暂停',
  superadmin: '超级管理员',
  admin: '管理员',
  manager: '经理',
  cashier: '收银员',
  todo: '待处理',
  'in progress': '进行中',
  done: '已完成',
  canceled: '已取消',
  backlog: '待规划',
  low: '低',
  medium: '中',
  high: '高',
  critical: '紧急',
  bug: '缺陷',
  feature: '功能',
  documentation: '文档',
  task: '任务',
  user: '用户',
  item: '记录',
  clicks: '点击量',
  uniques: '独立访客',
}

// Translate UI metadata only; never apply this to user names or chat bodies.
export function uiLabel(value: string): string {
  return labels[value] ?? value
}
