/**
 * 等待期的「俏皮话」配置与抽取逻辑。
 *
 * 为什么需要它：这个项目不是流式输出，一个阶段往往要等上一两分钟，
 * 中间界面只有一句「正在创建 js/app.js」在动。人在没有反馈的等待里
 * 会默认「是不是卡住了」，所以这里给每个阶段配一组轮换的短句——
 * 既说清楚现在在做什么，也让等待有点人味。
 *
 * 文案全部集中在本文件，改文案只需要动下面的 WAITING_LINES。
 * 改完建议跑 `npm run check:waiting`：它会校验每个阶段都有够数的文案、
 * 轮播不会重复、文案长度与用词符合界面与语气要求。
 *
 * 本文件不依赖 Vue，可被 `scripts/waiting-lines-check.mjs` 直接编译断言。
 */

/** 四个执行阶段，与后端 progress 事件的 stage 字段、界面阶段指示器一致 */
export type WaitingStage = 'planning' | 'coding' | 'verifying' | 'repairing'

export const WAITING_STAGES: WaitingStage[] = ['planning', 'coding', 'verifying', 'repairing']

/**
 * 配置被改空时的最后兜底。宁可显示一句没信息量的话，也不能让界面空一行——
 * 空行会被读成「前端挂了」。
 */
export const FALLBACK_WAITING_LINE = '正在处理'

/** 一个阶段的文案桶 */
export interface WaitingLineBucket {
  lines: string[]
}

export interface WaitingLinesConfig {
  /** 每隔多少毫秒换一句 */
  rotateMs: number
  stages: Record<WaitingStage, WaitingLineBucket>
  /** 还没收到阶段标识（流程刚起步）时用 */
  unknown: WaitingLineBucket
}

/**
 * 每阶段 15 条、10 秒一条 → 一轮 2.5 分钟不重样。
 *
 * 有意**不做「久等就换一套安抚文案」的分级**：分级意味着前 45 秒一种语气、
 * 之后突然换调子，反而像流程出了状况。这里把「报告进展」和「安抚情绪」的
 * 句子混在同一个池里打散，从开工第一秒就在轮，语气始终一致。
 */
export const WAITING_LINES: WaitingLinesConfig = {
  rotateMs: 10000,
  stages: {
    planning: {
      lines: [
        '正在把你的想法翻译成清单',
        '先想清楚再动手，省得返工',
        '把「大概要什么」变成「做什么」',
        '验收标准正在一条条列清楚',
        '正在给这次开发画路线图',
        '技术选型这一段最费脑子',
        '我在脑内先跑了一遍流程',
        '需求拆小一点，后面会顺很多',
        '这份计划要经得起验收',
        '正在挑最合适的实现方式',
        '好计划值得多花两分钟',
        '思路已经理到一半了',
        '把边界情况先想一遍',
        '在想要不要多留一个扩展口',
        '马上给你一份能签字的计划',
      ],
    },
    coding: {
      lines: [
        '文件一个一个写出来，别急',
        '先搭骨架，细节随后就到',
        '代码在长，请稍等片刻',
        '这块逻辑写完我又读了一遍',
        '变量名我也纠结了两秒',
        '写完这块就轮到下一块',
        '正在把想法敲成能跑的代码',
        '手上这活，键盘正热',
        '每一层结构都在按计划落位',
        '注释我也一并写了',
        '这段有点绕，我拆成两半写',
        '写代码这事，快不来才写得对',
        '进度条没停，我一直在写',
        '删掉了一些用不上的代码',
        '再写两个文件就能跑起来了',
      ],
    },
    verifying: {
      lines: [
        '正在真的打开页面点一遍',
        '按钮和输入框一个都不放过',
        '按验收清单逐条核对中',
        '跑得起来才算数，我在看',
        '截图留证，方便你回头看',
        '交互细节也要挨个试过去',
        '换个窗口尺寸再试一次',
        '正在确认页面干不干净',
        '我把每条验收都当真在验',
        '看看有没有能更顺手的地方',
        '刷新、返回、再进来都试了',
        '验收这件事，点得慢才点得准',
        '快好了，最后再检查一遍',
        '正在把证据一条条收齐',
        '验收不过我是不会签字的',
      ],
    },
    repairing: {
      lines: [
        '发现几处可以更好，正在改',
        '补上短板，马上回来',
        '把上一轮的小毛病收一收',
        '改完这一处就再验一遍',
        '打磨细节，就快回来了',
        '返工也是把事情做对的一环',
        '正在把没做到位的地方补齐',
        '改好后我会再自己验一遍',
        '小地方最花时间，但也最值得',
        '正在对着验收意见逐条改',
        '改完这一版应该就顺了',
        '宁可多改一会儿，也要改对',
        '相关的地方也一起收拾了',
        '正在收尾，很快回来',
        '这一轮改完就差最后一步',
      ],
    },
  },
  unknown: {
    lines: [
      '工程师们已经在路上了',
      '正在准备这次开发',
      '先整理一下手头的活',
      '马上就开工，稍等一下',
      '正在安排合适的工程师',
      '一切就绪，准备开始',
      '工具箱已经打开',
      '正在分工，很快到位',
      '先看一眼你要做的东西',
      '工位已就绪，正在接手',
    ],
  },
}

/**
 * 取当前该用哪个文案池。
 *
 * 返回的是配置里的数组本身（不是拷贝），调用方可以靠引用判断「池是否变了」。
 * 任何情况下都不返回空数组——配置被改坏时退回兜底句。
 */
export function resolvePool(
  stage: string | null | undefined,
  cfg: WaitingLinesConfig = WAITING_LINES
): string[] {
  const bucket: WaitingLineBucket | undefined =
    (cfg.stages as Record<string, WaitingLineBucket | undefined>)[String(stage ?? '')] ||
    cfg.unknown
  const pool = bucket?.lines
  return pool && pool.length ? pool : [FALLBACK_WAITING_LINE]
}

function shuffled(list: string[], rng: () => number): string[] {
  const arr = list.slice()
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.min(arr.length - 1, Math.floor(rng() * (i + 1)))
    const tmp = arr[i]
    arr[i] = arr[j]
    arr[j] = tmp
  }
  return arr
}

/**
 * 文案轮播器：一轮之内不重复，一轮走完重新洗牌。
 *
 * 为什么不直接随机取：纯随机会连续撞同一句（十几条池里连撞两次的概率不低），
 * 等待本来就焦虑，看到「还在写，进度一直往前走」重复三遍会像卡住了。
 * 洗牌袋保证一轮里句句不同，同时保留随机感。
 */
export function createLineRotator(
  cfg: WaitingLinesConfig = WAITING_LINES,
  rng: () => number = Math.random
) {
  let stage = ''
  let pool: string[] = []
  let bag: string[] = []

  return {
    /** 切换阶段：下一次取文案时会换池并重新洗牌 */
    setStage(next: string | null | undefined) {
      stage = String(next ?? '')
      // 置空而非立即解析：保持 next() 是唯一的池解析点
      pool = []
      bag = []
    },
    /** 取下一句 */
    next(): string {
      const target = resolvePool(stage, cfg)
      if (target !== pool) {
        pool = target
        bag = []
      }
      if (!bag.length) bag = shuffled(pool, rng)
      return bag.pop() ?? FALLBACK_WAITING_LINE
    },
    reset() {
      bag = []
    },
  }
}
