<script setup lang="ts">
import { ref, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import StorageSettings from '../components/StorageSettings.vue'
import OrgSettings from '../components/OrgSettings.vue'
import FingerprintPoolSettings from '../components/FingerprintPoolSettings.vue'
import BrowserImageSettings from '../components/BrowserImageSettings.vue'
import NetworkEgressSettings from '../components/NetworkEgressSettings.vue'

const isEE = __EE__
const SsoConfigPanel = isEE
  ? defineAsyncComponent(() => import('@ee/components/SsoConfigPanel.vue'))
  : null
const TenantManager = isEE
  ? defineAsyncComponent(() => import('@ee/components/TenantManager.vue'))
  : null

const { t } = useI18n()
const activeTab = ref('organization')
</script>

<template>
  <div class="flex flex-1 overflow-hidden">
      <div class="flex-1 overflow-y-scroll">
      <div class="max-w-4xl mx-auto px-6 py-8">
        <h2 class="text-lg font-semibold mb-6">{{ t('settings.title') }}</h2>
        
        <div class="flex flex-col md:flex-row gap-8">
          <!-- Left Navigation -->
          <nav class="flex md:flex-col gap-1 md:w-[200px] shrink-0 overflow-x-auto pb-2 md:pb-0">
            <button
              @click="activeTab = 'organization'"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'organization' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.organization') }}
            </button>
            <button
              @click="activeTab = 'storage'"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'storage' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.fileStorage') }}
            </button>
            <button
              @click="activeTab = 'fingerprintPool'"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'fingerprintPool' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.fingerprintPool') }}
            </button>
            <button
              @click="activeTab = 'browserImages'"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'browserImages' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.browserImages') }}
            </button>
            <button
              @click="activeTab = 'networkEgress'"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'networkEgress' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.networkEgress') }}
            </button>
            <button
              v-if="isEE"
              @click="activeTab = 'sso'"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'sso' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.sso', 'SSO') }}
            </button>
            <button
              v-if="isEE"
              @click="activeTab = 'tenants'"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'tenants' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.tenants', 'Tenants') }}
            </button>
          </nav>
          
          <!-- Main Content Area -->
          <div class="flex-1 min-w-0">
            <OrgSettings v-if="activeTab === 'organization'" />
            <StorageSettings v-else-if="activeTab === 'storage'" />
            <FingerprintPoolSettings v-else-if="activeTab === 'fingerprintPool'" />
            <BrowserImageSettings v-else-if="activeTab === 'browserImages'" />
            <NetworkEgressSettings v-else-if="activeTab === 'networkEgress'" />
            <component v-else-if="isEE && activeTab === 'sso' && SsoConfigPanel" :is="SsoConfigPanel" />
            <component v-else-if="isEE && activeTab === 'tenants' && TenantManager" :is="TenantManager" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
