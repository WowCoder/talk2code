<template>
  <div class="home-page">
    <AppNav />
    <main class="home-main">
      <HeroSection />
      <div class="home-input-wrap">
        <RequirementInput ref="inputRef" />
        <ExampleChips @select="onSelectExample" />
      </div>
    </main>
    <!-- 输入框下方给几条真实作品：一句"你要做什么"之前，先让人看见别人做成了什么。
         全宽背景区，与上方窄栏输入区形成节奏对比。 -->
    <MarketTeaser />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import AppNav from '@/components/layout/AppNav.vue'
import HeroSection from '@/components/home/HeroSection.vue'
import RequirementInput from '@/components/home/RequirementInput.vue'
import ExampleChips from '@/components/home/ExampleChips.vue'
import MarketTeaser from '@/components/home/MarketTeaser.vue'

const inputRef = ref<InstanceType<typeof RequirementInput>>()

function onSelectExample(example: string) {
  if (inputRef.value) {
    inputRef.value.content = example
  }
}
</script>

<style scoped>
.home-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--surface);
}

.home-main {
  flex: 1;
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  padding: 0 24px;
}

.home-input-wrap {
  margin-top: 16px;
}
</style>
