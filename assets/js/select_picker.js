import {requestDescriptionTooltip, dismissTooltip} from "./tooltips"

// An enhancement of the ordinary form select; the native input remains the form bridge.
const SelectPicker = {
  mounted() {
    this.input = document.getElementById(this.el.dataset.inputId)
    this.trigger = this.el.querySelector("button")
    this.panel = this.el.querySelector("[role=listbox]")
    this.popup = this.el.querySelector("[popover]")
    this.context = this.el.dataset.contextId
    this.onTrigger = () => this.opened ? this.close(true) : this.open()
    this.onKey = event => this.keydown(event)
    this.onOutside = event => {
      if (!this.el.contains(event.target)) this.close(false)
    }
    this.onOtherOpen = event => {
      if (event.detail !== this.el.id) this.close(false)
    }
    this.onChoice = event => {
      const option = event.target.closest("[role=option]")
      if (option) this.choose(Number(option.dataset.index))
      else if (event.target.closest("[data-picker-action]")) this.reveal()
    }
    this.onFocus = event => {
      if (!this.el.contains(event.target)) this.close(false)
    }
    this.schedulePosition = () => {
      cancelAnimationFrame(this.positionFrame)
      this.positionFrame = requestAnimationFrame(() => this.position())
    }
    this.trigger.addEventListener("click", this.onTrigger)
    this.popup.addEventListener("click", this.onChoice)
    document.addEventListener("keydown", this.onKey, true)
    document.addEventListener("pointerdown", this.onOutside, true)
    document.addEventListener("focusin", this.onFocus)
    document.addEventListener("form-picker-open", this.onOtherOpen)
    document.addEventListener("scroll", this.schedulePosition, true)
    window.addEventListener("resize", this.schedulePosition)
    this.resizeObserver = new ResizeObserver(this.schedulePosition)
    this.resizeObserver.observe(this.trigger)
    this.el.hidden = false
    this.sync()
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
    this.trigger.removeEventListener("click", this.onTrigger)
    this.popup.removeEventListener("click", this.onChoice)
    document.removeEventListener("keydown", this.onKey, true)
    document.removeEventListener("pointerdown", this.onOutside, true)
    document.removeEventListener("focusin", this.onFocus)
    document.removeEventListener("form-picker-open", this.onOtherOpen)
    document.removeEventListener("scroll", this.schedulePosition, true)
    window.removeEventListener("resize", this.schedulePosition)
  },

  sync() {
    dismissTooltip()
    const popupFocused = this.popup.contains(document.activeElement)
    const actionFocused = document.activeElement === this.popup.querySelector("[data-picker-action]")
    const previousValues = new Set((this.options || []).map(option => option.value))
    const visible = JSON.parse(this.el.dataset.visibleValues || "null")
    const metadata = JSON.parse(this.el.dataset.optionMetadata || "{}")
    this.allOptions = JSON.parse(this.el.dataset.options)
    this.options = this.allOptions.filter(option => !visible || visible.includes(option.value))
      .map(option => ({...option, ...metadata[option.value]}))
    this.action = JSON.parse(this.el.dataset.action || "null")
    // Data attributes are patched even on the ignored subtree. They also avoid relying on
    // browser selection retention when LiveView patches an unfocused native select.
    this.input.value = this.el.dataset.value
    this.trigger.setAttribute("aria-invalid", this.el.dataset.invalid)
    this.renderSelection()
    if (this.opened) {
      const revealCompleted = this.pendingReveal && !this.action
      const actionRemoved = actionFocused && !this.action
      const revealedIndex = revealCompleted || actionRemoved ?
        this.options.findIndex(option => !previousValues.has(option.value)) : -1
      this.active = revealedIndex >= 0 ? revealedIndex :
        (this.pendingReveal || actionFocused) && this.action ? this.options.length : this.selectedIndex()
      if (revealCompleted) this.pendingReveal = false
      this.renderOptions()
      this.position()
      if (popupFocused || revealCompleted || this.pendingReveal) {
        this.focusActive()
        this.describeActive()
      }
    }
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
    this.panel.replaceChildren(...this.options.map((option, index) => {
      const row = this.el.querySelector("template").content.firstElementChild.cloneNode(true)
      const selected = option.value === this.input.value
      // Caller values may contain whitespace, which cannot appear in an ARIA ID reference.
      row.id = `${this.input.id}-option-${encodeURIComponent(option.value)}`
      row.dataset.index = index
      row.dataset.value = option.value
      row.dataset.additional = String(!!option.additional)
      if (option.description) {
        const text = document.createElement("span")
        text.id = `${row.id}-description`
        text.hidden = true
        text.textContent = option.description
        row.append(text)
        row.setAttribute("aria-describedby", text.id)
        row.dataset.tooltip = option.description
        row.dataset.tooltipMode = "description"
      }
      row.dataset.active = String(index === this.active)
      row.setAttribute("aria-selected", String(selected))
      row.querySelector("[data-option-label]").textContent = option.label
      row.querySelector(".hero-check").hidden = !selected
      row.classList.toggle("bg-indigo-400/15", selected)
      row.classList.toggle("text-indigo-100", selected)
      row.classList.toggle("text-slate-200", !selected)
      row.classList.toggle("hover:bg-slate-800", !selected)
      row.classList.toggle("ring-2", index === this.active)
      row.classList.toggle("ring-inset", index === this.active)
      row.classList.toggle("ring-indigo-400/50", index === this.active)
      return row
    }))
    this.popup.querySelector("[data-picker-action]")?.remove()
    if (this.action) {
      const action = document.createElement("button")
      action.type = "button"
      action.id = `${this.input.id}-reveal`
      action.dataset.pickerAction = ""
      action.className = "select-picker-action"
      action.textContent = this.action.label
      this.popup.append(action)
    }
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
    if (!this.opened) {
      if (event.target !== this.trigger || !["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      this.open()
      return
    }
    if (event.key === "Tab") {
      this.close(true)
      return
    }
    if (!["Escape", "Enter", " ", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) &&
        !(event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey)) return
    event.preventDefault()
    event.stopImmediatePropagation()
    switch (event.key) {
      case "Escape": this.close(true); return
      case "Enter": case " ":
        if (this.active === this.options.length) this.reveal()
        else this.choose(this.active)
        return
      case "ArrowDown": this.active = (this.active + 1) % (this.options.length + (this.action ? 1 : 0)); break
      case "ArrowUp": {
        const count = this.options.length + (this.action ? 1 : 0)
        this.active = (this.active - 1 + count) % count
        break
      }
      case "Home": this.active = 0; break
      case "End": this.active = this.options.length - 1; break
      default: {
        const now = Date.now()
        this.search = (now - (this.searchAt || 0) > 700 ? "" : this.search) + event.key.toLocaleLowerCase()
        this.searchAt = now
        const index = this.options.findIndex(option => option.label.toLocaleLowerCase().startsWith(this.search))
        if (index >= 0) this.active = index
      }
    }
    this.renderOptions()
    this.focusActive()
    this.describeActive()
  },

  position() {
    if (!this.opened) return
    const anchor = this.trigger.getBoundingClientRect()
    // Close when scrolling the anchor out of its independently scrolling editor.
    for (let parent = this.trigger.parentElement; parent; parent = parent.parentElement) {
      if (["auto", "scroll", "hidden", "clip"].includes(getComputedStyle(parent).overflowY)) {
        const bounds = parent.getBoundingClientRect()
        if (anchor.bottom <= bounds.top || anchor.top >= bounds.bottom) return this.close(false)
      }
    }
    const margin = 12, gap = 8
    const below = innerHeight - anchor.bottom - margin - gap
    const above = anchor.top - margin - gap
    this.popup.style.width = `${Math.min(anchor.width, innerWidth - 2 * margin)}px`
    this.popup.style.maxHeight = `${Math.max(0, Math.min(240, Math.max(above, below)))}px`
    const panel = this.popup.getBoundingClientRect()
    const top = below >= panel.height || below >= above ? anchor.bottom + gap : anchor.top - gap - panel.height
    this.popup.style.left = `${Math.max(margin, Math.min(anchor.left, innerWidth - panel.width - margin))}px`
    this.popup.style.top = `${Math.max(margin, Math.min(top, innerHeight - panel.height - margin))}px`
  }
}

export default SelectPicker
