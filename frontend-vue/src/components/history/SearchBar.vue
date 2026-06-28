<template>
  <div class="search-bar">
    <input
      v-model="query"
      type="text"
      class="input-field search-input"
      placeholder="搜索项目…"
      @input="onSearch"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  search: [query: string]
}>()

const query = ref('')

function onSearch() {
  emit('search', query.value.trim().toLowerCase())
}
</script>

<style scoped>
.search-bar {
  margin-bottom: 20px;
}

.search-input {
  padding: 10px 14px;
  font-size: 14px;
}
</style>
