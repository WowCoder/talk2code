<template>
  <div class="example-chips">
    <span class="chips-hint">不知道写什么？试试这些</span>
    <span
      v-for="example in examples"
      :key="example.label"
      class="chip"
      @click="$emit('select', example.prompt)"
    >
      {{ example.label }}
    </span>
  </div>
</template>

<script setup lang="ts">
defineEmits<{
  select: [value: string]
}>()

// 示例统一为纯前端能跑通全链路（生成 → Playwright 真实验收）的需求：
// 不放依赖后端服务/CLI 的例子，避免「示例点进去验收必挂」的坑。
const examples = [
  {
    label: '贪吃蛇小游戏',
    prompt:
      '做一个能在浏览器里玩的 HTML5 贪吃蛇小游戏，要求：方向键控制移动、吃到食物蛇身变长、撞墙或撞到自己判定失败、有分数和重新开始按钮。',
  },
  {
    label: '数据看板 Demo',
    prompt:
      '做一个纯前端的销售数据看板页面：内置一组示例 JSON 数据，用图表展示月度趋势和分类占比，支持切换时间范围，数据在本地计算不需要后端。',
  },
  {
    label: 'Canvas 动画 Demo',
    prompt:
      '做一个 Canvas 粒子动画演示页：粒子跟随鼠标移动并相互连线，提供滑杆调节粒子数量与连线距离，画面流畅无卡顿。',
  },
  {
    label: 'SVG 图表编辑器',
    prompt:
      '做一个简单的 SVG 柱状图编辑器：可以在页面上编辑数据数值和标签，实时预览柱状图，支持导出为 SVG 文件。',
  },
  {
    label: '打字速度测试',
    prompt:
      '做一个打字速度测试小工具：给出一段中文短文，用户在输入框里照着打，实时显示正确率、已用时间和每分钟字数，结束后显示成绩。',
  },
]
</script>

<style scoped>
.example-chips {
  display: flex;
  gap: 10px;
  justify-content: center;
  align-items: center;
  margin-top: 20px;
  flex-wrap: wrap;
}

.chips-hint {
  font-size: 12.5px;
  color: var(--faint);
  margin-right: 2px;
}

.chip {
  padding: 8px 18px;
  border: 1px solid var(--border);
  border-radius: 100px;
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--surface);
  user-select: none;
}

.chip:hover {
  border-color: var(--accent);
  color: var(--accent-strong);
  background: var(--accent-soft);
}
</style>
