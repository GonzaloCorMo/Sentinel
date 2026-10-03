<script setup lang="ts">
import { RouterView } from "vue-router";
import { Toaster } from "vue-sonner";
</script>

<template>
  <RouterView />
  <Toaster position="top-right" :rich-colors="true" :close-button="true" />
</template>

<style>
html,
body,
#app {
  min-height: 100vh;
  margin: 0;
}
</style>
