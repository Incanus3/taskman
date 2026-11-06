import {requestDescriptionTooltip, dismissTooltip} from "./tooltips"

// An enhancement of the ordinary form select; the native input remains the form bridge.
const SelectPicker = {
  mounted() {
    this.findElements()
    this.createEventHandlers()
    this.listenToPicker()
    this.listenToDocument()
    this.observePosition()
    this.el.hidden = false
    this.sync()
  },

  findElements() {
    this.input = document.getElementById(this.el.dataset.inputId)
    this.trigger = this.el.querySelector("button")
    this.panel = this.el.querySelector("[role=listbox]")
    this.popup = this.el.querySelector("[popover]")
    this.context = this.el.dataset.contextId
  },

  createEventHandlers() {
    this.onTrigger = () => this.opened ? this.close(true) : this.open()
    this.onKey = event => this.keydown(event)
    this.onOutside = event => this.closeOutside(event)
    this.onOtherOpen = event => {
      if (event.detail !== this.el.id) this.close(false)
    }
    this.onChoice = event => this.chooseFromClick(event)
    this.onFocus = event => this.closeOutside(event)
    this.schedulePosition = () => {
      cancelAnimationFrame(this.positionFrame)
      this.positionFrame = requestAnimationFrame(() => this.position())
    }
  },

  closeOutside(event) {
    if (!this.el.contains(event.target)) this.close(false)
  },

  chooseFromClick(event) {
    const option = event.target.closest("[role=option]")
    if (option) this.choose(Number(option.dataset.index))
    else if (event.target.closest("[data-picker-action]")) this.reveal()
  },

  listenToPicker() {
    this.trigger.addEventListener("click", this.onTrigger)
    this.popup.addEventListener("click", this.onChoice)
  },

  listenToDocument() {
    document.addEventListener("keydown", this.onKey, true)
    document.addEventListener("pointerdown", this.onOutside, true)
    document.addEventListener("focusin", this.onFocus)
    document.addEventListener("form-picker-open", this.onOtherOpen)
  },

  observePosition() {
    document.addEventListener("scroll", this.schedulePosition, true)
    window.addEventListener("resize", this.schedulePosition)
    this.resizeObserver = new ResizeObserver(this.schedulePosition)
    this.resizeObserver.observe(this.trigger)
  },

  updated() {
    if (this.context !== this.el.dataset.contextId) {
      this.pendingReveal = false
      this.close(false)
    }
    this.context = this.el.dataset.contextId
    this.sync()
  },

  disconnected() {
    this.close(false)
    this.trigger.disabled = true
  },

  reconnected() {
    this.trigger.disabled = false
    this.sync()
  },

  destroyed() {
    this.close(false)
    cancelAnimationFrame(this.positionFrame)
    this.resizeObserver.disconnect()
    this.stopListeningToPicker()
    this.stopListeningToDocument()
    this.stopObservingPosition()
  },

  stopListeningToPicker() {
    this.trigger.removeEventListener("click", this.onTrigger)
    this.popup.removeEventListener("click", this.onChoice)
  },

  stopListeningToDocument() {
    document.removeEventListener("keydown", this.onKey, true)
    document.removeEventListener("pointerdown", this.onOutside, true)
    document.removeEventListener("focusin", this.onFocus)
    document.removeEventListener("form-picker-open", this.onOtherOpen)
  },

  stopObservingPosition() {
    document.removeEventListener("scroll", this.schedulePosition, true)
    window.removeEventListener("resize", this.schedulePosition)
  },

  sync() {
    dismissTooltip()
    const state = this.popupState()
    this.syncOptions()
    this.syncInput()
    this.renderSelection()
    if (this.opened) this.syncOpenPopup(state)
  },

  popupState() {
    return {
      popupFocused: this.popup.contains(document.activeElement),
      actionFocused: document.activeElement === this.popup.querySelector("[data-picker-action]"),
      previousValues: new Set((this.options || []).map(option => option.value))
    }
  },

  syncOptions() {
    const visible = JSON.parse(this.el.dataset.visibleValues || "null")
    const metadata = JSON.parse(this.el.dataset.optionMetadata || "{}")
    this.allOptions = JSON.parse(this.el.dataset.options)
    this.options = this.allOptions.filter(option => !visible || visible.includes(option.value))
      .map(option => ({...option, ...metadata[option.value]}))
    this.action = JSON.parse(this.el.dataset.action || "null")
  },

  syncInput() {
    // Data attributes are patched even on the ignored subtree. They also avoid relying on
    // browser selection retention when LiveView patches an unfocused native select.
    this.input.value = this.el.dataset.value
    this.trigger.setAttribute("aria-invalid", this.el.dataset.invalid)
  },

  syncOpenPopup(state) {
    const revealCompleted = this.syncActiveOption(state)
    this.renderOptions()
    this.position()
    if (state.popupFocused || revealCompleted || this.pendingReveal) {
      this.focusActive()
      this.describeActive()
    }
  },

  syncActiveOption({actionFocused, previousValues}) {
    const revealCompleted = this.pendingReveal && !this.action
    const actionRemoved = actionFocused && !this.action
    const revealedIndex = revealCompleted || actionRemoved ?
      this.options.findIndex(option => !previousValues.has(option.value)) : -1
    this.active = revealedIndex >= 0 ? revealedIndex :
      (this.pendingReveal || actionFocused) && this.action ? this.options.length : this.selectedIndex()
    if (revealCompleted) this.pendingReveal = false
    return revealCompleted
  },

  selectedIndex() {
    return Math.max(0, this.options.findIndex(option => option.value === this.input.value))
  },

  renderSelection() {
    this.el.querySelector("[data-picker-label]").textContent =
      this.allOptions.find(option => option.value === this.input.value)?.label || ""
  },

  open() {
    if (this.trigger.disabled) return
    document.dispatchEvent(new CustomEvent("form-picker-open", {detail: this.el.id}))
    this.opened = true
    this.search = ""
    this.active = this.selectedIndex()
    this.renderOptions()
    this.trigger.setAttribute("aria-expanded", "true")
    this.popup.showPopover()
    this.position()
    this.focusOpenedPopup()
  },

  focusOpenedPopup() {
    // The hidden popup has no scrollable layout until it enters the top layer and is sized.
    this.panel.children[this.active].scrollIntoView({block: "nearest"})
    this.panel.focus({preventScroll: true})
    this.describeActive()
  },

  close(restoreFocus) {
    if (!this.opened) return
    dismissTooltip()
    cancelAnimationFrame(this.descriptionFrame)
    this.opened = false
    this.pendingReveal = false
    this.popup.hidePopover()
    this.trigger.setAttribute("aria-expanded", "false")
    this.panel.removeAttribute("aria-activedescendant")
    if (restoreFocus && this.trigger.isConnected) this.trigger.focus({preventScroll: true})
  },

  renderOptions() {
    dismissTooltip()
    this.panel.replaceChildren(...this.options.map((option, index) => this.optionRow(option, index)))
    this.renderAction()
    this.renderActiveDescendant()
  },

  optionRow(option, index) {
    const row = this.el.querySelector("template").content.firstElementChild.cloneNode(true)
    // Caller values may contain whitespace, which cannot appear in an ARIA ID reference.
    row.id = `${this.input.id}-option-${encodeURIComponent(option.value)}`
    row.dataset.index = index
    row.dataset.value = option.value
    row.dataset.additional = String(!!option.additional)
    if (option.description) this.renderOptionDescription(row, option.description)
    this.renderOptionSelection(row, option, index)
    return row
  },

  renderOptionDescription(row, description) {
    const text = document.createElement("span")
    text.id = `${row.id}-description`
    text.hidden = true
    text.textContent = description
    row.append(text)
    row.setAttribute("aria-describedby", text.id)
    row.dataset.tooltip = description
    row.dataset.tooltipMode = "description"
  },

  renderOptionSelection(row, option, index) {
    const selected = option.value === this.input.value
    row.dataset.active = String(index === this.active)
    row.setAttribute("aria-selected", String(selected))
    row.querySelector("[data-option-label]").textContent = option.label
    row.querySelector(".hero-check").hidden = !selected
    row.classList.toggle("bg-indigo-400/15", selected)
    row.classList.toggle("text-indigo-100", selected)
    row.classList.toggle("text-slate-200", !selected)
    row.classList.toggle("hover:bg-slate-800", !selected)
    this.renderOptionActive(row, index === this.active)
  },

  renderOptionActive(row, active) {
    row.classList.toggle("ring-2", active)
    row.classList.toggle("ring-inset", active)
    row.classList.toggle("ring-indigo-400/50", active)
  },

  renderAction() {
    this.popup.querySelector("[data-picker-action]")?.remove()
    if (!this.action) return
    const action = document.createElement("button")
    action.type = "button"
    action.id = `${this.input.id}-reveal`
    action.dataset.pickerAction = ""
    action.className = "select-picker-action"
    action.textContent = this.action.label
    this.popup.append(action)
  },

  renderActiveDescendant() {
    const activeRow = this.panel.children[this.active]
    if (activeRow) {
      this.panel.setAttribute("aria-activedescendant", activeRow.id)
      activeRow.scrollIntoView({block: "nearest"})
    } else {
      this.panel.removeAttribute("aria-activedescendant")
    }
  },

  describeActive() {
    cancelAnimationFrame(this.descriptionFrame)
    this.descriptionFrame = requestAnimationFrame(() => {
      if (this.opened) requestDescriptionTooltip(this.panel.children[this.active])
    })
  },

  focusActive() {
    const target = this.active === this.options.length ?
      this.popup.querySelector("[data-picker-action]") : this.panel
    target?.focus({preventScroll: true})
  },

  reveal() {
    if (!this.action || this.pendingReveal) return
    this.pendingReveal = true
    this.pushEvent(this.action.event, {})
  },

  choose(index) {
    const value = this.options[index].value
    const changed = this.input.value !== value
    this.input.value = value
    this.renderSelection()
    this.close(true)
    if (changed) this.input.dispatchEvent(new Event("change", {bubbles: true}))
  },

  keydown(event) {
    if (!this.el.contains(event.target)) return
    if (!this.opened) return this.openFromKey(event)
    if (event.key === "Tab") {
      this.close(true)
      return
    }
    if (!this.handlesKey(event)) return
    this.consumeKey(event)
    this.handleOpenKey(event.key)
  },

  openFromKey(event) {
    if (event.target !== this.trigger || !["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) return
    this.consumeKey(event)
    this.open()
  },

  handlesKey(event) {
    return ["Escape", "Enter", " ", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) ||
      (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey)
  },

  consumeKey(event) {
    event.preventDefault()
    event.stopImmediatePropagation()
  },

  handleOpenKey(key) {
    if (key === "Escape") return this.close(true)
    if (key === "Enter" || key === " ") {
      if (this.active === this.options.length) this.reveal()
      else this.choose(this.active)
      return
    }
    this.navigateOptions(key)
    this.renderOptions()
    this.focusActive()
    this.describeActive()
  },

  navigateOptions(key) {
    if (key === "ArrowDown" || key === "ArrowUp") {
      this.moveActive(key === "ArrowDown" ? 1 : -1)
    } else if (key === "Home" || key === "End") {
      this.active = key === "Home" ? 0 : this.options.length - 1
    } else {
      this.searchOptions(key)
    }
  },

  moveActive(offset) {
    const count = this.options.length + (this.action ? 1 : 0)
    this.active = offset > 0 ? (this.active + 1) % count : (this.active - 1 + count) % count
  },

  searchOptions(key) {
    const now = Date.now()
    this.search = (now - (this.searchAt || 0) > 700 ? "" : this.search) + key.toLocaleLowerCase()
    this.searchAt = now
    const index = this.options.findIndex(option => option.label.toLocaleLowerCase().startsWith(this.search))
    if (index >= 0) this.active = index
  },

  position() {
    if (!this.opened) return
    const anchor = this.trigger.getBoundingClientRect()
    if (this.anchorClipped(anchor)) return this.close(false)
    const space = this.sizePopup(anchor)
    this.positionPopup(anchor, space)
  },

  anchorClipped(anchor) {
    // Close when scrolling the anchor out of its independently scrolling editor.
    for (let parent = this.trigger.parentElement; parent; parent = parent.parentElement) {
      if (["auto", "scroll", "hidden", "clip"].includes(getComputedStyle(parent).overflowY)) {
        const bounds = parent.getBoundingClientRect()
        if (anchor.bottom <= bounds.top || anchor.top >= bounds.bottom) return true
      }
    }
    return false
  },

  sizePopup(anchor) {
    const margin = 12
    const gap = 8
    const below = innerHeight - anchor.bottom - margin - gap
    const above = anchor.top - margin - gap
    this.popup.style.width = `${Math.min(anchor.width, innerWidth - 2 * margin)}px`
    this.popup.style.maxHeight = `${Math.max(0, Math.min(240, Math.max(above, below)))}px`
    return {margin, gap, below, above}
  },

  positionPopup(anchor, {margin, gap, below, above}) {
    const panel = this.popup.getBoundingClientRect()
    const top = below >= panel.height || below >= above ? anchor.bottom + gap : anchor.top - gap - panel.height
    this.popup.style.left = `${Math.max(margin, Math.min(anchor.left, innerWidth - panel.width - margin))}px`
    this.popup.style.top = `${Math.max(margin, Math.min(top, innerHeight - panel.height - margin))}px`
  }
}

export default SelectPicker
