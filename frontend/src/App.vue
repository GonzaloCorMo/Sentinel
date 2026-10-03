<script setup lang="ts">
import { RouterView } from "vue-router";
import { Toaster } from "vue-sonner";
import { useTheme } from "@/composables/useTheme";

const { theme } = useTheme();
</script>

<template>
  <RouterView />
  <Toaster position="top-right" :theme="theme" :close-button="true" />
</template>
