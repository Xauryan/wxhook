import { useDeferredValue, useRef, useState } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  ContactRound,
  LoaderCircle,
  Mars,
  MessageCircle,
  MoreHorizontal,
  Pencil,
  Phone,
  RefreshCw,
  Search,
  UsersRound,
  Venus,
  Video,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import type { LiveStatus, Session } from './api'
import { ContactAvatar, Notice } from './chat-ui'
import { contactsApi, type Contact, type RemarkRequest } from './contacts-api'
import './contacts.css'

function regionText(contact: Contact) {
  const labels: Record<string, string> = {
    CN: '中国',
    Guangdong: '广东',
    Zhaoqing: '肇庆',
    Guangzhou: '广州',
    Shenzhen: '深圳',
    Beijing: '北京',
    Shanghai: '上海',
    Zhejiang: '浙江',
    Jiangsu: '江苏',
    Sichuan: '四川',
    Hubei: '湖北',
    Hunan: '湖南',
    Fujian: '福建',
    Shandong: '山东',
    Henan: '河南',
    Hebei: '河北',
    Anhui: '安徽',
    Jiangxi: '江西',
    Guangxi: '广西',
    Hainan: '海南',
    Guizhou: '贵州',
    Yunnan: '云南',
    Shaanxi: '陕西',
    Chongqing: '重庆',
    Tianjin: '天津',
  }
  const region = contact.region
  return region
    ? [
        ...new Set(
          [
            region.country === 'CN' && region.province ? '' : region.country,
            region.province,
            region.city,
          ]
            .filter(Boolean)
            .map((value) => labels[value] || value)
        ),
      ].join(' ')
    : ''
}

function RemarkDialog({
  account,
  contact,
  connected,
  close,
  refresh,
}: {
  account: string
  contact: Contact
  connected: boolean
  close: () => void
  refresh: () => void
}) {
  const [value, setValue] = useState(contact.remark)
  const [attempt, setAttempt] = useState<RemarkRequest>()
  const mutation = useMutation({
    mutationFn: (request: RemarkRequest) =>
      contactsApi.remark(contact.username, request),
    retry: false,
    onSuccess: (result) => {
      if (result.status === 'confirmed') refresh()
    },
  })
  const uncertain = ['pending', 'unknown', 'dispatching'].includes(
    mutation.data?.status || ''
  )
  const confirmed = mutation.data?.status === 'confirmed'
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) close()
      }}
    >
      <DialogContent className='contact-remark-dialog'>
        <DialogHeader>
          <DialogTitle>修改备注</DialogTitle>
          <DialogDescription>
            为 {contact.nickname || contact.display_name}{' '}
            修改真实微信备注。只有通讯录回读一致后才会显示成功。
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            const request =
              uncertain && attempt
                ? attempt
                : {
                    account,
                    remark: value,
                    expected_remark: contact.remark,
                    request_id: crypto.randomUUID(),
                  }
            setAttempt(request)
            mutation.mutate(request)
          }}
        >
          <label htmlFor='wechat-contact-remark'>备注名</label>
          <Input
            id='wechat-contact-remark'
            maxLength={64}
            value={value}
            disabled={mutation.isPending || uncertain || confirmed}
            onChange={(event) => setValue(event.target.value)}
            autoFocus
          />
          <small>留空可清除备注，最多 64 个字符。</small>
          {mutation.error && <p role='alert'>{mutation.error.message}</p>}
          {mutation.data && (
            <p role='status' className={confirmed ? 'contact-confirmed' : ''}>
              {mutation.data.detail}
            </p>
          )}
          <div className='contact-dialog-actions'>
            <Button
              type='button'
              variant='outline'
              disabled={mutation.isPending}
              onClick={close}
            >
              {confirmed ? '完成' : '取消'}
            </Button>
            {!confirmed && (
              <Button
                type='submit'
                disabled={
                  !connected ||
                  mutation.isPending ||
                  (!uncertain && value === contact.remark)
                }
              >
                {mutation.isPending
                  ? '正在核验…'
                  : uncertain
                    ? '重新核验'
                    : '保存到微信'}
              </Button>
            )}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function ContactDetails({
  contact,
  connected,
  edit,
  openChat,
}: {
  contact: Contact
  connected: boolean
  edit: () => void
  openChat: (session: Session) => void
}) {
  const [groupsOpen, setGroupsOpen] = useState(false)
  return (
    <div className='contact-profile'>
      <header className='contact-profile-header'>
        <ContactAvatar name={contact.display_name} url={contact.avatar_url} />
        <div className='contact-profile-identity'>
          <h2>
            {contact.display_name}
            {contact.gender === 'female' ? (
              <Venus className='contact-gender female' aria-label='女' />
            ) : contact.gender === 'male' ? (
              <Mars className='contact-gender male' aria-label='男' />
            ) : null}
          </h2>
          {contact.remark && contact.nickname !== contact.remark && (
            <p>昵称：{contact.nickname || '本地未提供'}</p>
          )}
          <p>微信号：{contact.alias || contact.username}</p>
          <p>地区：{regionText(contact) || '本地未提供'}</p>
          {contact.is_self && <small>当前登录账号</small>}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className='contact-more' aria-label='联系人更多操作'>
              <MoreHorizontal size={22} />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align='end'>
            <DropdownMenuItem
              disabled={!connected || !contact.can_edit_remark}
              onSelect={edit}
            >
              <Pencil size={15} />
              修改备注
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>
      <section className='contact-profile-section'>
        <h3>朋友资料</h3>
        <dl>
          <dt>备注</dt>
          <dd>
            {contact.can_edit_remark ? (
              <button
                className='contact-edit-link'
                disabled={!connected}
                onClick={edit}
              >
                {contact.remark || '添加备注名'}
                <Pencil size={13} />
              </button>
            ) : (
              contact.remark || '未设置'
            )}
          </dd>
          {contact.description && (
            <>
              <dt>描述</dt>
              <dd>{contact.description}</dd>
            </>
          )}
        </dl>
      </section>
      <section className='contact-profile-section'>
        <dl>
          <dt>朋友圈</dt>
          <dd className='contact-unavailable'>可从左侧朋友圈菜单浏览本地动态</dd>
          <dt>视频号</dt>
          <dd className='contact-unavailable'>本地未提供可核验的视频号资料</dd>
        </dl>
      </section>
      <section className='contact-profile-section'>
        <h3>更多信息</h3>
        <dl>
          <dt>共同群聊</dt>
          <dd>
            {contact.common_groups_known ? (
              <button
                className='contact-group-toggle'
                disabled={!contact.common_groups.length}
                aria-expanded={groupsOpen}
                onClick={() => setGroupsOpen(!groupsOpen)}
              >
                {contact.common_groups.length} 个
                {contact.common_groups.length > 0 &&
                  (groupsOpen ? (
                    <ChevronDown size={15} />
                  ) : (
                    <ChevronRight size={15} />
                  ))}
              </button>
            ) : (
              '本地群成员资料未就绪'
            )}
          </dd>
          <dt>个性签名</dt>
          <dd>
            {contact.signature || (
              <span className='contact-unavailable'>本地未提供</span>
            )}
          </dd>
          <dt>来源</dt>
          <dd>
            {contact.source_label ||
              (contact.source_scene == null
                ? '本地未提供'
                : `微信来源代码 ${contact.source_scene}`)}
          </dd>
          <dt>添加时间</dt>
          <dd>{contact.added_date || '本地未提供'}</dd>
        </dl>
        {groupsOpen && (
          <ul className='contact-common-groups'>
            {contact.common_groups.map((group) => (
              <li key={group.username}>
                <button
                  onClick={() =>
                    openChat({
                      ...group,
                      is_group: true,
                      message_count: 0,
                      can_send: true,
                    })
                  }
                >
                  <UsersRound size={20} />
                  <span>{group.display_name}</span>
                  <ChevronRight size={16} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <div className='contact-profile-actions'>
        <button
          disabled={!connected || !contact.can_send}
          onClick={() =>
            openChat({
              username: contact.username,
              display_name: contact.display_name,
              avatar_url: contact.avatar_url,
              is_group: false,
              message_count: 0,
              can_send: contact.can_send,
            })
          }
        >
          <MessageCircle />
          <span>发消息</span>
        </button>
        <button
          disabled
          title='语音通话暂未接入'
          aria-label='语音聊天（暂未接入）'
        >
          <Phone />
          <span>语音聊天</span>
          <small>暂未接入</small>
        </button>
        <button
          disabled
          title='视频通话暂未接入'
          aria-label='视频聊天（暂未接入）'
        >
          <Video />
          <span>视频聊天</span>
          <small>暂未接入</small>
        </button>
      </div>
    </div>
  )
}

export function ContactsWorkspace({
  live,
  selected,
  select,
  openChat,
}: {
  live: LiveStatus
  selected: string | null
  select: (username: string | null) => void
  openChat: (session: Session) => void
}) {
  const account = live.account
  const client = useQueryClient()
  const [search, setSearch] = useState('')
  const query = useDeferredValue(search.trim())
  const [mode, setMode] = useState<'detail' | 'manage' | 'new-friends'>(
    'detail'
  )
  const [expanded, setExpanded] = useState(true)
  const [editing, setEditing] = useState<Contact | null>(null)
  const anchors = useRef(new Map<string, HTMLElement>())
  const list = useInfiniteQuery({
    queryKey: ['wechat-contacts', account, 'list', query],
    queryFn: ({ signal, pageParam }) =>
      contactsApi.list(account, query, pageParam, signal),
    initialPageParam: 0,
    enabled: !!account,
    staleTime: 30_000,
    retry: false,
    refetchInterval: (current) =>
      current.state.data?.pages[0]?.ready === false ? 2000 : false,
    getNextPageParam: (last, pages) => {
      const count = pages.reduce((sum, page) => sum + page.items.length, 0)
      return last.items.length && count < last.total ? count : undefined
    },
  })
  const detail = useQuery({
    queryKey: ['wechat-contacts', account, 'detail', selected],
    queryFn: ({ signal }) => contactsApi.detail(account, selected!, signal),
    enabled: !!account && !!selected && mode === 'detail',
    staleTime: 30_000,
    retry: false,
  })
  const requests = useQuery({
    queryKey: ['wechat-contacts', account, 'new-friends'],
    queryFn: ({ signal }) => contactsApi.newFriends(account, signal),
    enabled: !!account && mode === 'new-friends',
    staleTime: 20_000,
    retry: false,
  })
  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ['wechat-contacts', account] })
    void client.invalidateQueries({ queryKey: ['wechat-roster-v2', account] })
    void client.invalidateQueries({ queryKey: ['wechat-live-status-v2'] })
  }
  const refresh = useMutation({
    mutationFn: () => contactsApi.refresh(account),
    onSuccess: invalidate,
    retry: false,
  })
  const items = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.items) || []).map((item) => [
        item.username,
        item,
      ])
    ).values(),
  ]
  const letters = [...new Set(items.map((item) => item.initial))]
  const first = list.data?.pages[0]
  const hasSelection = mode !== 'detail' || !!selected
  const choose = (username: string) => {
    select(username)
    setMode('detail')
  }
  return (
    <div
      className={`contacts-window ${hasSelection ? 'has-contact-selection' : ''}`}
    >
      <aside className='contacts-panel' aria-label='微信通讯录'>
        <div className='contacts-search-row'>
          <div className='session-search'>
            <Search size={15} />
            <Input
              aria-label='搜索通讯录'
              placeholder='搜索'
              maxLength={200}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            {search && (
              <button aria-label='清空通讯录搜索' onClick={() => setSearch('')}>
                <X size={14} />
              </button>
            )}
          </div>
        </div>
        <button
          className={`contacts-manage ${mode === 'manage' ? 'selected' : ''}`}
          onClick={() => setMode('manage')}
        >
          <UsersRound size={21} />
          通讯录管理
        </button>
        <button
          className={`contacts-section-toggle ${mode === 'new-friends' ? 'selected' : ''}`}
          aria-pressed={mode === 'new-friends'}
          onClick={() => setMode('new-friends')}
        >
          <ChevronRight size={17} />
          新的朋友
        </button>
        <button
          className='contacts-section-toggle'
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
          联系人<span>{first?.ready ? first.directory_count : '待核验'}</span>
        </button>
        <div className='contacts-list-area'>
          <div className='contacts-list' aria-busy={list.isFetching}>
            {list.error && (
              <Notice error={list.error} retry={() => void list.refetch()} />
            )}
            {!list.error && (!first?.ready || list.isPending) && (
              <div className='chat-state'>
                <LoaderCircle className={account ? 'spin' : ''} />
                <p>
                  {account
                    ? first?.contacts_error || '正在核验当前微信通讯录…'
                    : '请登录微信并启动桥接'}
                </p>
              </div>
            )}
            {expanded && first?.ready && !list.error && (
              <>
                {items.length === 0 && (
                  <p className='contacts-empty-list'>
                    {search ? '没有匹配的联系人' : '当前通讯录暂无联系人'}
                  </p>
                )}
                {letters.map((letter) => (
                  <section
                    key={letter}
                    ref={(node) => {
                      if (node) anchors.current.set(letter, node)
                      else anchors.current.delete(letter)
                    }}
                  >
                    <h3 className='contact-letter'>{letter}</h3>
                    {items
                      .filter((item) => item.initial === letter)
                      .map((item) => (
                        <button
                          key={item.username}
                          className={`contact-list-item ${selected === item.username && mode === 'detail' ? 'selected' : ''}`}
                          aria-pressed={
                            selected === item.username && mode === 'detail'
                          }
                          aria-label={`查看联系人：${item.display_name}`}
                          onClick={() => choose(item.username)}
                        >
                          <ContactAvatar
                            name={item.display_name}
                            url={item.avatar_url}
                          />
                          <span>{item.display_name}</span>
                          {item.is_self && <small>我</small>}
                        </button>
                      ))}
                  </section>
                ))}
              </>
            )}
            {expanded && list.hasNextPage && (
              <Button
                variant='ghost'
                disabled={list.isFetching}
                onClick={() => void list.fetchNextPage()}
              >
                加载更多联系人
              </Button>
            )}
          </div>
          {expanded && letters.length > 2 && (
            <nav className='contact-letter-index' aria-label='通讯录字母索引'>
              {letters.map((letter) => (
                <button
                  key={letter}
                  aria-label={`跳到 ${letter} 分组`}
                  onClick={() =>
                    anchors.current
                      .get(letter)
                      ?.scrollIntoView({ block: 'start' })
                  }
                >
                  {letter}
                </button>
              ))}
            </nav>
          )}
        </div>
        <footer className='contacts-sync'>
          <span>
            {first?.updated_at
              ? `通讯录更新于 ${new Date(first.updated_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })}`
              : '使用当前账号真实通讯录'}
          </span>
          <button
            disabled={!account || !live.bridge_connected || refresh.isPending}
            aria-label='刷新通讯录'
            onClick={() => refresh.mutate()}
          >
            <RefreshCw size={15} className={refresh.isPending ? 'spin' : ''} />
          </button>
        </footer>
      </aside>
      <section
        className={`contacts-content ${!hasSelection ? 'contacts-content-empty' : ''}`}
        aria-label={
          mode === 'manage'
            ? '通讯录管理'
            : mode === 'new-friends'
              ? '新的朋友'
              : '联系人资料'
        }
      >
        <div className='contacts-content-toolbar'>
          <button
            className='contacts-back'
            aria-label='返回通讯录'
            onClick={() => {
              select(null)
              setMode('detail')
            }}
          >
            <ArrowLeft size={20} />
            通讯录
          </button>
          {!live.bridge_connected && (
            <p role='status'>微信未连接，暂不可修改备注或发消息</p>
          )}
        </div>
        {(refresh.error || first?.contacts_error) && (
          <p className='contacts-error' role='alert'>
            {refresh.error?.message || first?.contacts_error}
          </p>
        )}
        {mode === 'detail' && !selected && (
          <div className='contacts-blank'>
            <ContactRound size={70} strokeWidth={1} />
            <h2>通讯录</h2>
            <p>选择联系人，查看朋友资料</p>
          </div>
        )}
        {mode === 'detail' && selected && (
          <>
            {detail.isPending && (
              <div className='chat-state'>
                <LoaderCircle className='spin' />
                <p>正在读取联系人资料…</p>
              </div>
            )}
            {detail.error && (
              <Notice
                error={detail.error}
                retry={() => void detail.refetch()}
              />
            )}
            {detail.data && !detail.error && (
              <ContactDetails
                key={detail.data.username}
                contact={detail.data}
                connected={live.bridge_connected}
                edit={() => setEditing(detail.data!)}
                openChat={openChat}
              />
            )}
          </>
        )}
        {mode === 'manage' && (
          <div className='contacts-management'>
            <h2>通讯录管理</h2>
            <p>
              当前账号的真实联系人 ·{' '}
              {first?.ready ? `${first.directory_count} 人` : '正在核验'}
              {search && ` · 搜索“${search}”`}
            </p>
            {!first?.ready ? (
              <p>通讯录正在核验，请稍候。</p>
            ) : (
              <div className='contacts-management-scroll'>
                <table>
                  <thead>
                    <tr>
                      <th>联系人</th>
                      <th>微信号</th>
                      <th>备注</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.username}>
                        <td>
                          <button
                            className='management-contact-name'
                            onClick={() => choose(item.username)}
                          >
                            <ContactAvatar
                              name={item.display_name}
                              url={item.avatar_url}
                            />
                            {item.nickname || item.display_name}
                          </button>
                        </td>
                        <td>{item.alias || item.username}</td>
                        <td>{item.remark || '未设置'}</td>
                        <td>
                          <Button
                            variant='ghost'
                            size='sm'
                            disabled={
                              !live.bridge_connected || !item.can_edit_remark
                            }
                            onClick={() => setEditing(item)}
                          >
                            修改备注
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!items.length && <p>没有匹配的联系人。</p>}
              </div>
            )}
            {list.hasNextPage && (
              <Button
                variant='outline'
                disabled={list.isFetching}
                onClick={() => void list.fetchNextPage()}
              >
                加载更多联系人
              </Button>
            )}
          </div>
        )}
        {mode === 'new-friends' && (
          <div className='contacts-new-friends'>
            <h2>新的朋友</h2>
            <p>{requests.data?.detail || '正在读取当前账号的本地申请记录…'}</p>
            {requests.error && (
              <Notice
                error={requests.error}
                retry={() => void requests.refetch()}
              />
            )}
            {requests.data?.ready && (
              <>
                {requests.data.items.length === 0 && (
                  <p className='contacts-request-empty'>
                    本地快照暂无好友申请记录
                  </p>
                )}
                {requests.data.items.map((item) => (
                  <article key={item.id}>
                    <ContactAvatar
                      name={item.display_name}
                      url={item.avatar_url}
                    />
                    <div>
                      <strong>{item.display_name}</strong>
                      <p>{item.content}</p>
                      <time>
                        {item.timestamp > 0
                          ? new Date(item.timestamp * 1000).toLocaleString(
                              'zh-CN',
                              { hour12: false }
                            )
                          : '时间未知'}
                      </time>
                    </div>
                    {item.in_contacts ? (
                      <button onClick={() => choose(item.username)}>
                        查看联系人
                      </button>
                    ) : (
                      <span>历史申请</span>
                    )}
                  </article>
                ))}
              </>
            )}
          </div>
        )}
      </section>
      {editing && (
        <RemarkDialog
          key={editing.username}
          account={account}
          contact={editing}
          connected={live.bridge_connected}
          close={() => setEditing(null)}
          refresh={invalidate}
        />
      )}
    </div>
  )
}
