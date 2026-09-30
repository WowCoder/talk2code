/**
 * 工具操作的展示辅助：
 * 1) 历史数据里 readable 可能自带「⛔ 」前缀（旧版后端生成），而前端
 *    操作列表已经按 blocked 状态渲染 ⛔ 图标 —— 不去掉就会显示成
 *    「⛔ ⛔ 已跳过 read_file: …」。
 * 2) 操作参数此前直接 JSON.stringify 铺出来，对用户几乎没有信息量。
 *    这里按工具类型转成人话（文件路径 / 行数 / 命令首行）。
 */

/** 去掉 readable 开头重复的状态 emoji（⛔ / ❌），保留正文 */
export function stripStatusEmoji(label: string): string {
  return String(label || '').replace(/^[⛔❌]\s*/, '')
}

/** 截断到指定长度并加省略号 */
function truncate(text: string, max: number): string {
  const t = String(text || '').trim()
  return t.length > max ? t.slice(0, max) + '…' : t
}

/**
 * 把工具参数转成一行可读摘要；识别不了的降级为紧凑 JSON。
 * 只挑有信息量的字段，不再把整个参数对象原样铺给用户。
 */
export function describeToolArgs(
  toolName: string,
  args: Record<string, unknown> | undefined | null
): string {
  if (!args || typeof args !== 'object') return ''
  const a = args as Record<string, any>

  switch (toolName) {
    case 'write_file': {
      const content = typeof a.content === 'string' ? a.content : ''
      const lines = content ? content.split('\n').length : 0
      const size = content ? ` · ${lines} 行` : ''
      return `写入文件 ${a.filename || ''}${size}`
    }
    case 'edit_file': {
      const edits = a.edit ?? a.edits ?? ''
      const count = typeof edits === 'string' ? edits.split('<<<< SEARCH').length - 1 || 1 : 1
      return `编辑文件 ${a.filename || ''}（${count} 处修改）`
    }
    case 'read_file':
      return `读取文件 ${a.filename || ''}`
    case 'delete_file':
      return `删除文件 ${a.filename || ''}`
    case 'list_files':
      return `列出目录 ${a.directory || a.dir || '.'}`
    case 'execute_code': {
      const code = typeof a.code === 'string' ? a.code : ''
      const firstLine = code.split('\n').find((l) => l.trim()) || ''
      return firstLine ? `执行代码：${truncate(firstLine, 60)}` : '执行代码'
    }
    case 'validate_html':
      return `校验页面 ${a.filename || 'index.html'}`
    case 'lint_css':
    case 'lint_js':
      return `检查代码 ${a.filename || ''}`
    case 'fetch_cdn_library':
      return a.url ? `引入外部库 ${truncate(String(a.url), 60)}` : '引入外部库'
    case 'search_docs':
      return a.query ? `查文档：${truncate(String(a.query), 60)}` : '查文档'
    default: {
      // 未识别的工具：键值对形式给一行摘要，比整坨 JSON 可读
      const pairs = Object.entries(a)
        .map(([k, v]) => `${k}=${truncate(typeof v === 'string' ? v : JSON.stringify(v), 40)}`)
        .join('，')
      return truncate(pairs, 120)
    }
  }
}
