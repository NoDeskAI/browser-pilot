<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { setLocale, getLocale } from '../i18n'
import { useSessions } from '../composables/useSessions'
import { useAuth } from '../composables/useAuth'
import {
  LayoutGrid, Settings, Users, SquareTerminal, LogOut, Languages, User as UserIcon,
} from 'lucide-vue-next'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Badge } from '@/components/ui/badge'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const { brand } = useSessions()
const { user, logout } = useAuth()

const emit = defineEmits<{
  (e: 'open-cli-docs'): void
}>()

const showUsersLink = computed(() =>
  user.value && (user.value.role === 'superadmin' || user.value.role === 'admin'),
)

function handleLogout() {
  logout()
  router.push('/login')
}

function toggleLocale() {
  setLocale(getLocale() === 'zh' ? 'en' : 'zh')
}
</script>

<template>
  <header class="shrink-0 h-14 border-b border-border bg-background flex items-center justify-between px-2 sm:px-4 z-20">
    <!-- Left: Brand and Main Navigation -->
    <div class="flex items-center gap-2 sm:gap-4 md:gap-6 min-w-0">
      <div class="flex items-center gap-2 shrink-0">
        <span class="text-sm font-semibold truncate">{{ brand.appTitle }}</span>
        <Badge v-if="brand.edition === 'ee'" variant="outline" class="text-[10px] uppercase px-1 hidden sm:inline-flex">EE</Badge>
      </div>

      <nav class="flex items-center gap-1 shrink-0">
        <button
          @click="router.push('/')"
          class="flex items-center gap-2 px-2 md:px-3 py-1.5 rounded-md text-sm transition-colors"
          :class="route.path === '/' || route.path.startsWith('/s/') ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'"
          :title="t('session.sessions')"
        >
          <LayoutGrid class="size-4 shrink-0" />
          <span class="hidden md:inline">{{ t('session.sessions') }}</span>
        </button>
        <button
          v-if="showUsersLink"
          @click="router.push('/users')"
          class="flex items-center gap-2 px-2 md:px-3 py-1.5 rounded-md text-sm transition-colors"
          :class="route.path === '/users' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'"
          :title="t('users.title')"
        >
          <Users class="size-4 shrink-0" />
          <span class="hidden md:inline">{{ t('users.title') }}</span>
        </button>
      </nav>
    </div>

    <!-- Right: Secondary Navigation and User Menu -->
    <div class="flex items-center gap-1 sm:gap-2 shrink-0">
      <nav class="flex items-center gap-1 mr-1 sm:mr-2 md:mr-4">
        <button
          @click="emit('open-cli-docs')"
          class="flex items-center gap-2 px-2 md:px-3 py-1.5 rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          :title="t('app.cliAccess')"
        >
          <SquareTerminal class="size-4 shrink-0" />
          <span class="hidden md:inline">{{ t('app.cliAccess') }}</span>
        </button>
        <button
          @click="toggleLocale"
          class="flex items-center gap-2 px-2 md:px-3 py-1.5 rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          :title="getLocale() === 'zh' ? 'English' : '中文'"
        >
          <Languages class="size-4 shrink-0" />
          <span class="hidden lg:inline">{{ getLocale() === 'zh' ? 'English' : '中文' }}</span>
        </button>
        <button
          @click="router.push('/settings')"
          class="flex items-center gap-2 px-2 md:px-3 py-1.5 rounded-md text-sm transition-colors"
          :class="route.path === '/settings' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'"
          :title="t('app.settings')"
        >
          <Settings class="size-4 shrink-0" />
          <span class="hidden md:inline">{{ t('app.settings') }}</span>
        </button>
      </nav>

      <!-- User Dropdown -->
      <DropdownMenu>
        <DropdownMenuTrigger as-child>
          <button class="flex items-center gap-2 px-1 sm:px-2 py-1.5 rounded-md text-sm text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring" :title="user?.email">
            <span class="hidden sm:inline truncate text-xs max-w-[100px] xl:max-w-[150px]">{{ user?.email }}</span>
            <div class="size-7 rounded-full bg-accent text-foreground flex items-center justify-center text-xs font-medium shrink-0 border border-border">
              {{ user?.name?.charAt(0)?.toUpperCase() || '?' }}
            </div>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="bottom" align="end" class="w-56 mt-1">
          <div class="px-2 py-1.5">
            <p class="text-sm font-medium truncate">{{ user?.name }}</p>
            <p class="text-xs text-muted-foreground truncate">{{ user?.email }}</p>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem @click="router.push('/account')" class="cursor-pointer">
            <UserIcon class="size-4 mr-2" />
            {{ t('auth.accountSettings') }}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem @click="handleLogout" class="cursor-pointer text-destructive focus:text-destructive">
            <LogOut class="size-4 mr-2" />
            {{ t('auth.logout') }}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  </header>
</template>
