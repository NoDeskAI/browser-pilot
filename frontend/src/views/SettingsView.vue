<script setup lang="ts">
import { computed, defineAsyncComponent, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import StorageSettings from '../components/StorageSettings.vue'
import OrgSettings from '../components/OrgSettings.vue'
import FingerprintPoolSettings from '../components/FingerprintPoolSettings.vue'
import BrowserImageSettings from '../components/BrowserImageSettings.vue'
import NetworkEgressSettings from '../components/NetworkEgressSettings.vue'
import { useSessions } from '../composables/useSessions'

const isEE = __EE__
const SsoConfigPanel = isEE
  ? defineAsyncComponent(() => import('@ee/components/SsoConfigPanel.vue'))
  : null
const TenantManager = isEE
  ? defineAsyncComponent(() => import('@ee/components/TenantManager.vue'))
  : null

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { brand } = useSessions()
const browserImagesEnabled = computed(() => brand.features.browserImages !== false)

type SettingsTab = 'organization' | 'storage' | 'fingerprintPool' | 'browserImages' | 'networkEgress' | 'sso' | 'tenants'

const sectionToTab: Record<string, SettingsTab> = {
  organization: 'organization',
  storage: 'storage',
  'fingerprint-pool': 'fingerprintPool',
  'browser-images': 'browserImages',
  'network-egress': 'networkEgress',
  sso: 'sso',
  tenants: 'tenants',
}

const tabToPath: Record<SettingsTab, string> = {
  organization: '/settings',
  storage: '/settings/storage',
  fingerprintPool: '/settings/fingerprint-pool',
  browserImages: '/settings/browser-images',
  networkEgress: '/settings/network-egress',
  sso: '/settings/sso',
  tenants: '/settings/tenants',
}

function normalizeSection(section: string | string[] | undefined): string | undefined {
  return Array.isArray(section) ? section[0] : section
}

function resolveTab(section: string | string[] | undefined): SettingsTab | null {
  const normalized = normalizeSection(section)
  if (!normalized) return 'organization'
  const tab = sectionToTab[normalized]
  if (!tab) return null
  if (!isEE && (tab === 'sso' || tab === 'tenants')) return null
  if (tab === 'browserImages' && !browserImagesEnabled.value) return null
  return tab
}

const activeTab = computed<SettingsTab>(() => resolveTab(route.params.section) || 'organization')

watch(
  () => route.params.section,
  (section) => {
    if (normalizeSection(section) && !resolveTab(section)) {
      router.replace('/settings')
    }
  },
  { immediate: true },
)

function goToTab(tab: SettingsTab) {
  router.push(tabToPath[tab])
}
</script>

<template>
  <div class="flex flex-1 overflow-hidden">
    <div class="flex-1 overflow-y-scroll">
      <div class="max-w-[68rem] mx-auto px-6 py-8">
        <h2 class="text-lg font-semibold mb-6">{{ t('settings.title') }}</h2>
        
        <div class="flex flex-col md:flex-row gap-8">
          <!-- Left Navigation -->
          <nav class="flex md:flex-col gap-1 md:w-[200px] shrink-0 overflow-x-auto pb-2 md:pb-0">
            <button
              @click="goToTab('organization')"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'organization' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.organization') }}
            </button>
            <button
              @click="goToTab('storage')"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'storage' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.fileStorage') }}
            </button>
            <button
              @click="goToTab('fingerprintPool')"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'fingerprintPool' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.fingerprintPool') }}
            </button>
            <button
              v-if="browserImagesEnabled"
              @click="goToTab('browserImages')"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'browserImages' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.browserImages') }}
            </button>
            <button
              @click="goToTab('networkEgress')"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'networkEgress' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.networkEgress') }}
            </button>
            <button
              v-if="isEE"
              @click="goToTab('sso')"
              class="px-3 py-2 text-sm rounded-md transition-colors text-left whitespace-nowrap"
              :class="activeTab === 'sso' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
            >
              {{ t('settings.sso', 'SSO') }}
            </button>
            <button
              v-if="isEE"
              @click="goToTab('tenants')"
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
