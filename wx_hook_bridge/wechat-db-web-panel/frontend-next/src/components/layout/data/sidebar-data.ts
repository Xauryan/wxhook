import {
  Activity,
  Bell,
  BookOpen,
  Bot,
  ChartNoAxesCombined,
  ContactRound,
  Database,
  Download,
  FileSearch,
  Images,
  ListTodo,
  MessagesSquare,
  Network,
  Package,
  Palette,
  Search,
} from 'lucide-react'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  navGroups: [
    {
      title: '工作台',
      items: [
        { title: '微信聊天', url: '/chats', icon: MessagesSquare },
        { title: '任务管理', url: '/tasks', icon: ListTodo },
        { title: '自动化', url: '/apps', icon: Package },
        { title: 'AI 群助手', url: '/group-assistant', icon: Bot },
        { title: 'AI 接入', url: '/ai', icon: Network },
        { title: '外观设置', url: '/settings/appearance', icon: Palette },
      ],
    },
    {
      title: '数据分析',
      items: [
        {
          title: '数据概览',
          url: '/analysis/overview',
          icon: ChartNoAxesCombined,
        },
        { title: '联系人分析', url: '/analysis/contacts', icon: ContactRound },
        { title: '关系分析', url: '/analysis/relationships', icon: Network },
        { title: '媒体统计', url: '/analysis/media', icon: Images },
        { title: '活跃热力图', url: '/analysis/contributions', icon: Activity },
        {
          title: '会话分析与导出',
          url: '/analysis/conversations',
          icon: FileSearch,
        },
        { title: '会话统计', url: '/analysis/chat-list', icon: MessagesSquare },
        { title: '全文搜索', url: '/analysis/search', icon: Search },
        { title: '报告中心', url: '/analysis/reports', icon: BookOpen },
        { title: '导出中心', url: '/analysis/exports', icon: Download },
        { title: '本机提醒', url: '/analysis/notifications', icon: Bell },
        { title: '数据库诊断', url: '/analysis/settings', icon: Database },
      ],
    },
  ],
}
