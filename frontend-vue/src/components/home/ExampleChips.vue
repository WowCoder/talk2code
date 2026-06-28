<template>
  <div class="example-chips">
    <span
      v-for="(example, index) in examples"
      :key="index"
      class="chip"
      @click="$emit('select', example)"
    >
      {{ example }}
    </span>
  </div>
</template>

<script setup lang="ts">
defineEmits<{
  select: [value: string]
}>()

const examples = [
  '创建一个待办事项列表应用',
  '创建一个简单计算器',
  '创建一个笔记记录应用',
]
</script>

<style scoped>
.example-chips {
  display: flex;
  gap: 10px;
  justify-content: center;
  margin-top: 20px;
  flex-wrap: wrap;
}

.chip {
  padding: 8px 18px;
  border: 1px solid var(--border);
  border-radius: 100px;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--surface);
  user-select: none;
}

.chip:hover {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
</style>
