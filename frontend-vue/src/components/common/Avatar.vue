<template>
  <div :class="['avatar', size]">
    {{ initial }}
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  username: string
  size?: 'sm' | 'md' | 'lg'
}>(), {
  size: 'md',
})

const initial = computed(() => (props.username || '?')[0]?.toUpperCase())

const sizeMap: Record<string, Record<string, string>> = {
  sm: { width: '24px', height: '24px', fontSize: '10px' },
  md: { width: '30px', height: '30px', fontSize: '12px' },
  lg: { width: '64px', height: '64px', fontSize: '24px' },
}
</script>

<style scoped>
.avatar {
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  flex-shrink: 0;
  width: v-bind('sizeMap[size].width');
  height: v-bind('sizeMap[size].height');
  font-size: v-bind('sizeMap[size].fontSize');
}
</style>
