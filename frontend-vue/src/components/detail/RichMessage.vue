<template>
  <div class="rich-message" :class="{ collapsed: foldable && needsFold && !expanded }">
    <template v-for="(block, bi) in blocks" :key="bi">
      <!-- 标题 -->
      <div
        v-if="block.type === 'heading'"
        class="rm-heading"
        :class="'rm-h' + block.level"
      ><RichInline :tokens="block.tokens" /></div>

      <!-- 代码块 -->
      <pre v-else-if="block.type === 'code'" class="rm-codeblock"><code>{{ block.text }}</code></pre>

      <!-- 引用 -->
      <blockquote v-else-if="block.type === 'quote'" class="rm-quote"><RichInline :tokens="block.tokens" /></blockquote>

      <!-- 列表 -->
      <ul v-else-if="block.type === 'list' && !block.ordered" class="rm-list">
        <li v-for="(item, li) in block.items" :key="li"><RichInline :tokens="item" /></li>
      </ul>
      <ol v-else-if="block.type === 'list' && block.ordered" class="rm-list rm-ol">
        <li v-for="(item, li) in block.items" :key="li"><RichInline :tokens="item" /></li>
      </ol>

      <!-- 分隔线 -->
      <hr v-else-if="block.type === 'hr'" class="rm-hr" />

      <!-- 段落 -->
      <p v-else class="rm-paragraph"><RichInline :tokens="(block as any).tokens" /></p>
    </template>

    <!-- 折叠渐隐遮罩 + 展开按钮 -->
    <div v-if="foldable && needsFold && !expanded" class="rm-fade" @click="expanded = true">
      <button class="rm-toggle" type="button" @click.stop="expanded = true">展开全文 ↓</button>
    </div>
    <button
      v-if="foldable && needsFold && expanded"
      class="rm-toggle rm-toggle-collapse"
      type="button"
      @click="expanded = false"
    >收起 ↑</button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import RichInline, { type InlineToken } from './RichInline.vue'

/**
 * 轻量 Markdown 渲染（仅行级 + 内联，不引入第三方 markdown 库）。
 *
 * 安全约束：绝不使用 v-html 渲染模型/用户内容。文本经 inline tokenizer 拆成
 * {text|bold|code} 片段后用 Vue 模板渲染，从根本上杜绝 XSS。
 *
 * 折叠：长消息默认只显示前 foldLines 行（CSS max-height 截断 + 渐隐遮罩），
 * 点击「展开全文」查看完整内容——满足「排版 + 默认折叠前几行」诉求。
 */
const props = withDefaults(
  defineProps<{
    content: string
    foldable?: boolean
    foldLines?: number
    foldChars?: number
  }>(),
  { foldable: true, foldLines: 6, foldChars: 500 }
)

const expanded = ref(false)

type Block =
  | { type: 'heading'; level: number; tokens: InlineToken[] }
  | { type: 'paragraph'; tokens: InlineToken[] }
  | { type: 'code'; lang: string; text: string }
  | { type: 'list'; ordered: boolean; items: InlineToken[][] }
  | { type: 'quote'; tokens: InlineToken[] }
  | { type: 'hr' }

function inlineTokens(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  const regex = /(\*\*([^*]+)\*\*|`([^`]+)`)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = regex.exec(text))) {
    if (m.index > last) tokens.push({ type: 'text', value: text.slice(last, m.index) })
    if (m[2] !== undefined) tokens.push({ type: 'bold', value: m[2] })
    else if (m[3] !== undefined) tokens.push({ type: 'code', value: m[3] })
    last = regex.lastIndex
  }
  if (last < text.length) tokens.push({ type: 'text', value: text.slice(last) })
  return tokens
}

const blocks = computed<Block[]>(() => {
  const text = props.content || ''
  const lines = text.split('\n')
  const out: Block[] = []
  let para: string[] = []
  const flushPara = () => {
    if (para.length) {
      out.push({ type: 'paragraph', tokens: inlineTokens(para.join('\n')) })
      para = []
    }
  }
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.trim() === '') {
      flushPara()
      i++
      continue
    }
    if (line.trim().startsWith('```')) {
      flushPara()
      const lang = line.trim().slice(3).trim()
      const code: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        code.push(lines[i])
        i++
      }
      i++ // skip closing fence
      out.push({ type: 'code', lang, text: code.join('\n') })
      continue
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      flushPara()
      out.push({ type: 'heading', level: h[1].length, tokens: inlineTokens(h[2]) })
      i++
      continue
    }
    const ul = line.match(/^[-*]\s+(.*)$/)
    if (ul) {
      flushPara()
      const items: InlineToken[][] = []
      while (i < lines.length) {
        const mm = lines[i].match(/^[-*]\s+(.*)$/)
        if (!mm) break
        items.push(inlineTokens(mm[1]))
        i++
      }
      out.push({ type: 'list', ordered: false, items })
      continue
    }
    const ol = line.match(/^\d+\.\s+(.*)$/)
    if (ol) {
      flushPara()
      const items: InlineToken[][] = []
      while (i < lines.length) {
        const mm = lines[i].match(/^\d+\.\s+(.*)$/)
        if (!mm) break
        items.push(inlineTokens(mm[1]))
        i++
      }
      out.push({ type: 'list', ordered: true, items })
      continue
    }
    const q = line.match(/^>\s+(.*)$/)
    if (q) {
      flushPara()
      out.push({ type: 'quote', tokens: inlineTokens(q[1]) })
      i++
      continue
    }
    if (/^---+$/.test(line.trim())) {
      flushPara()
      out.push({ type: 'hr' })
      i++
      continue
    }
    para.push(line)
    i++
  }
  flushPara()
  return out
})

const needsFold = computed(() => {
  if (!props.foldable) return false
  const text = props.content || ''
  const lineCount = text.split('\n').length
  return lineCount > props.foldLines || text.length > props.foldChars
})
</script>

<style scoped>
.rich-message {
  font-size: 14px;
  line-height: 1.6;
  color: var(--fg);
  word-break: break-word;
}

/* 折叠态：只显示前几行，超出部分截断 */
.rich-message.collapsed {
  max-height: calc(6 * 1.6em);
  overflow: hidden;
  position: relative;
}

.rm-heading {
  margin: 8px 0 4px;
  font-weight: 600;
  line-height: 1.4;
}
.rm-h1 { font-size: 1.15em; }
.rm-h2 { font-size: 1.08em; }
.rm-heading:first-child {
  margin-top: 0;
}

.rm-paragraph {
  margin: 4px 0;
  white-space: pre-wrap;
}

.rm-codeblock {
  background: var(--dark-bg, #1e293b);
  color: var(--dark-fg, #e2e8f0);
  padding: 10px 12px;
  border-radius: 6px;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre;
  margin: 6px 0;
}

.rm-quote {
  border-left: 3px solid var(--border);
  padding: 2px 12px;
  margin: 6px 0;
  color: var(--muted);
  white-space: pre-wrap;
}

.rm-list {
  margin: 4px 0;
  padding-left: 20px;
}
.rm-ol {
  list-style: decimal;
}
.rm-list li {
  margin: 2px 0;
}

.rm-hr {
  border: none;
  border-top: 1px solid var(--border);
  margin: 8px 0;
}

/* 折叠渐隐遮罩 + 展开按钮 */
.rm-fade {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: calc(2 * 1.6em);
  background: linear-gradient(to bottom, transparent, var(--bg, #fff) 85%);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  cursor: pointer;
}

.rm-toggle {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 999px;
  padding: 3px 14px;
  font-size: 12px;
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}
.rm-toggle-collapse {
  display: block;
  margin: 6px auto 0;
  background: var(--surface);
  color: var(--muted);
  border: 1px solid var(--border);
  box-shadow: none;
}
</style>
