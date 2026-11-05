class TooltipController {
  selector = "[data-tooltip]"
  delay = 450
  leaveDelay = 120
  gap = 9
  margin = 8
  trigger = null
  timer = null
  leaveTimer = null
  tooltip = null
  label = null
  pointerDown = false

  constructor() {
    this.listenToPointer()
    this.listenToFocus()
    this.listenForDismissal()
    this.observeSource()
  }

  listenToPointer() {
    document.addEventListener("pointerover", this.onPointerOver)
    document.addEventListener("pointermove", this.onPointerMove)
    document.addEventListener("pointerout", this.onPointerOut)
  }

  listenToFocus() {
    document.addEventListener("focusin", this.onFocusIn)
    document.addEventListener("focusout", this.onFocusOut)
  }

  listenForDismissal() {
    document.addEventListener("pointerdown", this.onPointerDown)
    document.addEventListener("pointerup", this.onPointerRelease)
    document.addEventListener("pointercancel", this.onPointerRelease)
    // Capture without consuming Escape: the owning picker/filter/dialog still handles it.
    document.addEventListener("keydown", this.onKeyDown, true)
    document.addEventListener("change", this.dismiss, true)
    document.addEventListener("scroll", this.dismiss, true)
    document.addEventListener("toggle", this.onToggle, true)
    document.addEventListener("phx:update", this.onPageUpdate)
    window.addEventListener("phx:page-loading-start", this.dismiss)
    window.addEventListener("resize", this.dismiss)
  }

  observeSource() {
    new MutationObserver(this.onMutation).observe(document.documentElement, {
      subtree: true, childList: true, attributes: true,
      attributeFilter: ["data-tooltip", "hidden", "open", "disabled", "aria-disabled"]
    })
  }

  dismiss = () => {
    clearTimeout(this.timer)
    clearTimeout(this.leaveTimer)
    this.timer = this.leaveTimer = null
    this.tooltip?.remove()
    this.tooltip = this.trigger = this.label = null
  }

  description(target) {
    return target?.dataset.tooltipMode === "description"
  }

  content(target) {
    return target.dataset.tooltip || target.getAttribute("aria-label")
  }

  valid(target) {
    return target?.isConnected && target.getClientRects().length > 0 &&
      !target.matches(":disabled, [aria-disabled='true']") &&
      !target.closest("[hidden]") &&
      (!target.closest("[popover]") || target.closest("[popover]").matches(":popover-open"))
  }

  show(target) {
    this.timer = null
    if (!this.valid(target) || this.content(target) !== this.label) return this.dismiss()
    this.createTooltip(target)
    this.positionTooltip(target)
  }

  createTooltip(target) {
    this.tooltip = document.createElement("div")
    this.tooltip.setAttribute("role", "tooltip")
    // Sources have their own persistent accessible description; visual timing is independent.
    this.tooltip.setAttribute("aria-hidden", "true")
    this.tooltip.className = "taskman-tooltip"
    this.tooltip.textContent = this.label
    if (this.description(target)) this.tooltip.setAttribute("popover", "manual")
    const container = target.closest("dialog") || document.body
    container.append(this.tooltip)
    if (this.description(target)) this.tooltip.showPopover()
  }

  positionTooltip(target) {
    const targetBounds = target.getBoundingClientRect()
    const tipBounds = this.tooltip.getBoundingClientRect()
    const above = targetBounds.top >= tipBounds.height + this.gap + this.margin
    const top = above
      ? targetBounds.top - tipBounds.height - this.gap
      : targetBounds.bottom + this.gap
    const left = targetBounds.left + (targetBounds.width - tipBounds.width) / 2
    this.tooltip.style.left = `${this.clampPosition(left, tipBounds.width, innerWidth)}px`
    this.tooltip.style.top = `${this.clampPosition(top, tipBounds.height, innerHeight)}px`
  }

  clampPosition(position, size, limit) {
    return Math.max(this.margin, Math.min(position, limit - size - this.margin))
  }

  schedule(target, immediate = false) {
    clearTimeout(this.leaveTimer)
    this.leaveTimer = null
    if (this.trigger === target) return this.finishDelay(target, immediate)
    this.dismiss()
    if (!this.valid(target)) return
    this.label = this.content(target)
    if (!this.label) return
    this.trigger = target
    if (immediate) this.show(target)
    else this.timer = setTimeout(() => this.show(target), this.delay)
  }

  finishDelay(target, immediate) {
    if (!immediate || !this.timer) return
    clearTimeout(this.timer)
    this.show(target)
  }

  requestDescription(target) {
    if (this.description(target)) this.schedule(target)
    else this.dismiss()
  }

  pointerSource(event) {
    const direct = event.target.closest(this.selector)
    if (direct) return direct
    // A noninteractive badge leaves the pointer on its enclosing row link. Hit test only
    // explicitly opted-in scopes, preserving that link as the actual event target.
    const scope = event.target.closest("[data-tooltip-scope]")
    return scope && [...scope.querySelectorAll("[data-tooltip-pass-through]")].find(source => {
      const r = source.getBoundingClientRect()
      return event.clientX >= r.left && event.clientX <= r.right &&
        event.clientY >= r.top && event.clientY <= r.bottom
    })
  }

  leaveSource() {
    if (this.description(this.trigger)) {
      // Allow crossing the small placement gap into the tooltip itself.
      if (!this.leaveTimer) this.leaveTimer = setTimeout(this.dismiss, this.leaveDelay)
    } else this.dismiss()
  }

  onPointerOver = event => {
    if (this.tooltip?.contains(event.target)) {
      clearTimeout(this.leaveTimer)
      this.leaveTimer = null
      return
    }
    const source = this.pointerSource(event)
    if (source) this.schedule(source)
    else if (this.trigger) this.leaveSource()
  }

  onPointerMove = event => {
    if (event.target.closest("[data-tooltip-scope]") || this.trigger?.hasAttribute("data-tooltip-pass-through")) {
      this.onPointerOver(event)
    }
  }

  onPointerOut = event => {
    if (!this.trigger || this.trigger.contains(event.relatedTarget) || this.tooltip?.contains(event.relatedTarget)) return
    this.leaveSource()
  }

  onFocusIn = event => {
    if (!this.pointerDown) {
      const target = event.target.closest(this.selector)
      this.schedule(target, !this.description(target))
    }
  }

  onFocusOut = event => {
    if (this.trigger?.contains(event.target)) this.dismiss()
  }

  onPointerDown = () => {
    this.pointerDown = true
    this.dismiss()
  }

  onPointerRelease = () => {
    this.pointerDown = false
  }

  onKeyDown = event => {
    if (event.key === "Escape") this.dismiss()
  }

  onToggle = event => {
    if (event.newState === "closed" && event.target.contains(this.trigger)) this.dismiss()
  }

  onPageUpdate = () => {
    if (this.description(this.trigger)) this.dismiss()
  }

  onMutation = () => {
    if (this.trigger && (!this.valid(this.trigger) || this.content(this.trigger) !== this.label)) this.dismiss()
  }
}

const tooltips = new TooltipController()

export function dismissTooltip() {
  tooltips.dismiss()
}

export function requestDescriptionTooltip(target) {
  tooltips.requestDescription(target)
}
