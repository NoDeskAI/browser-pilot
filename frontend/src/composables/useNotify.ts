import { toast } from 'vue-sonner'

export function useNotify() {
  return {
    success: (msg: string) => toast.success(msg),
    error: (msg: string) => toast.error(msg, { duration: 6000 }),
    info: (msg: string) => toast.info(msg),
    notify: (msg: string, type: 'success' | 'error' | 'info' = 'info') => {
      toast[type](msg)
    },
  }
}
