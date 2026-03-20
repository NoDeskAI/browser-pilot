declare module '@novnc/novnc' {
  export default class RFB {
    constructor(
      target: HTMLElement,
      urlOrChannel: string | WebSocket,
      options?: {
        shared?: boolean
        credentials?: { username?: string; password?: string; target?: string }
        repeaterID?: string
        wsProtocols?: string[]
      },
    )

    background: string
    capabilities: Readonly<{ power: boolean }>
    clipViewport: boolean
    clippingViewport: Readonly<boolean>
    compressionLevel: number
    dragViewport: boolean
    focusOnClick: boolean
    qualityLevel: number
    resizeSession: boolean
    scaleViewport: boolean
    viewOnly: boolean

    approveServer(): void
    blur(): void
    clipboardPasteFrom(text: string): void
    disconnect(): void
    focus(options?: FocusOptions): void
    getImageData(): ImageData
    machineReboot(): void
    machineReset(): void
    machineShutdown(): void
    sendCredentials(credentials: {
      username?: string
      password?: string
      target?: string
    }): void
    sendCtrlAltDel(): void
    sendKey(keysym: number, code: string | null, down?: boolean): void
    toBlob(
      callback: (blob: Blob) => void,
      type?: string,
      quality?: number,
    ): void
    toDataURL(type?: string, encoderOptions?: number): string

    addEventListener(type: 'bell', listener: (e: CustomEvent) => void): void
    addEventListener(
      type: 'capabilities',
      listener: (e: CustomEvent<{ capabilities: { power: boolean } }>) => void,
    ): void
    addEventListener(
      type: 'clipboard',
      listener: (e: CustomEvent<{ text: string }>) => void,
    ): void
    addEventListener(type: 'connect', listener: (e: CustomEvent) => void): void
    addEventListener(
      type: 'credentialsrequired',
      listener: (e: CustomEvent<{ types: string[] }>) => void,
    ): void
    addEventListener(
      type: 'desktopname',
      listener: (e: CustomEvent<{ name: string }>) => void,
    ): void
    addEventListener(
      type: 'disconnect',
      listener: (e: CustomEvent<{ clean: boolean }>) => void,
    ): void
    addEventListener(
      type: 'securityfailure',
      listener: (
        e: CustomEvent<{ status: number; reason?: string }>,
      ) => void,
    ): void
    addEventListener(type: string, listener: (e: CustomEvent) => void): void
    removeEventListener(
      type: string,
      listener: (e: CustomEvent) => void,
    ): void
  }
}
