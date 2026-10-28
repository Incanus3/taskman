import {Calendar, time} from "../vendor/vanilla-calendar-pro.js"

const localValue = value => {
  const trimmed = value.trim()
  if (!trimmed) return ""
  const human = trimmed.match(/^(\d{2})\.(\d{2})\.(\d{4}) (\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)$/)
  if (human) return `${human[3]}-${human[2]}-${human[1]}T${human[4]}`
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?$/.test(trimmed) ? trimmed : null
}

const displayValue = value => {
  if (!value) return ""
  const [date, time] = value.split("T")
  return `${date.split("-").reverse().join(".")} ${time}`
}

// The text field owns typing and form events; the calendar owns a disposable ISO local draft.
const DateTimePicker = {
  mounted() {
    this.input = document.getElementById(this.el.dataset.inputId)
    this.trigger = this.el.querySelector('[data-picker-role="trigger"]')
    this.panel = this.el.querySelector('[data-picker-role="popup"]')
    this.host = this.panel.querySelector("[data-calendar-host]")
    this.applyButton = this.el.querySelector('[data-picker-role="apply"]')
    this.notice = this.el.querySelector("[role=status]")
    this.context = this.el.dataset.contextId
    this.serverValue = this.el.dataset.value
    this.listen = (target, name, callback, options) => {
      target.addEventListener(name, callback, options)
      this.cleanup.push(() => target.removeEventListener(name, callback, options))
    }
    this.cleanup = []
    this.listen(this.trigger, "click", () => this.opened ? this.close(true) : this.open())
    this.listen(this.applyButton, "click", () => this.apply())
    this.listen(this.el.querySelector('[data-picker-role="cancel"]'), "click", () => this.close(true))
    this.listen(this.el.querySelector('[data-picker-role="clear"]'), "click", () => {
      this.cleared = true
      this.invalidTime = false
      this.calendar.set({selectedDates: [], selectedTime: "00:00"})
      this.updateDraft()
    })
    // Text controls commit on change; until then a visible edit cannot apply stale context.
    this.listen(this.panel, "input", event => {
      if (event.target.matches("[data-vc-time-input] input")) {
        this.invalidTime = true
        this.updateDraft()
      }
    })
    // Bubble-phase isolation lets the library handle its inputs first.
    for (const name of ["input", "change", "submit"]) {
      this.listen(this.panel, name, event => {
        event.stopPropagation()
        if (name === "submit") event.preventDefault()
      })
    }
    this.listen(document, "keydown", event => this.keydown(event), true)
    this.listen(document, "pointerdown", event => {
      if (this.opened && !this.el.contains(event.target)) this.close(false)
    }, true)
    this.listen(document, "focusin", event => {
      if (this.opened && !this.el.contains(event.target)) this.close(false)
    })
    this.listen(document, "form-picker-open", event => {
      if (event.detail !== this.el.id) this.close(false)
    })
    this.schedulePosition = () => {
      cancelAnimationFrame(this.positionFrame)
      this.positionFrame = requestAnimationFrame(() => this.position())
    }
    this.listen(document, "scroll", this.schedulePosition, true)
    this.listen(window, "resize", this.schedulePosition)
    this.listen(this.input, "input", () => this.enhance())
    this.listen(this.input, "change", () => this.enhance())
    this.resizeObserver = new ResizeObserver(this.schedulePosition)
    this.resizeObserver.observe(this.trigger)
    this.enhance()
  },

  updated() {
    const changed = this.serverValue !== this.el.dataset.value
    const navigated = this.context !== this.el.dataset.contextId
    if (this.opened && (changed || navigated)) {
      this.close(false)
      if (changed && !navigated) this.notice.textContent = this.el.dataset.changedMessage
    }
    this.serverValue = this.el.dataset.value
    this.context = this.el.dataset.contextId
    // Do not write the visible field here: LiveView preserves focused, partially typed input.
    this.enhance()
  },

  supported(value) {
    const iso = localValue(value)
    if (iso === null) return false
    if (!iso) return true
    if (Number(iso.slice(0, 4)) < 1000 || Number(iso.slice(0, 4)) > 9999) return false
    // Native parsing validates the local calendar date without timezone conversion.
    const probe = document.createElement("input")
    probe.type = "datetime-local"
    probe.value = iso
    return probe.value !== ""
  },

  enhance() {
    const supported = typeof this.panel.showPopover === "function" && !this.offline &&
      !this.input.readOnly && this.supported(this.input.value) && this.supported(this.el.dataset.value)
    this.trigger.hidden = !supported
    this.trigger.disabled = !supported
    if (!supported) this.close(false)
  },

  open() {
    if (this.trigger.disabled || !this.supported(this.input.value)) return
    document.dispatchEvent(new CustomEvent("form-picker-open", {detail: this.el.id}))
    this.original = localValue(this.input.value)
    this.cleared = false
    this.invalidTime = false
    this.notice.textContent = ""
    // destroy() restores its original node; only this throwaway child belongs to the library.
    const child = document.createElement("div")
    child.className = "vc"
    this.host.replaceChildren(child)
    this.calendar = new Calendar(child, {
      extensions: [time], selectionTimeMode: 24,
      dateMin: "1000-01-01", dateMax: "9999-12-31",
      enableJumpToSelectedDate: true, enableDateToggle: false,
      selectedDates: this.original ? [this.original.slice(0, 10)] : [],
      selectedTime: this.original ? this.original.slice(11, 16) : "00:00",
      onClickDate: () => { this.cleared = false; this.updateDraft() },
      onChangeTime: (_calendar, _event, isError) => {
        this.invalidTime = isError
        this.updateDraft()
      }
    })
    this.calendar.init()
    this.opened = true
    this.trigger.setAttribute("aria-expanded", "true")
    this.panel.showPopover()
    this.updateDraft()
    this.position()
    this.panel.focus({preventScroll: true})
  },

  updateDraft() {
    const {selectedDates, selectedTime} = this.calendar.context
    this.draft = this.cleared ? "" : `${selectedDates[0] || ""}T${selectedTime || ""}`
    const valid = this.cleared || (/^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d$/.test(this.draft) && !this.invalidTime)
    this.applyButton.disabled = !valid
  },

  apply() {
    if (!this.opened || this.applyButton.disabled) return
    // An unchanged minute selection preserves original seconds/fractions without a form event.
    const unchanged = this.draft === this.original || (!this.cleared && this.draft === this.original.slice(0, 16))
    const value = unchanged ? this.original : this.draft
    this.close(true)
    if (value !== this.original) {
      this.input.value = displayValue(value)
      this.input.dispatchEvent(new Event("input", {bubbles: true}))
    }
  },

  close(restoreFocus) {
    if (!this.opened) return
    this.opened = false
    this.panel.hidePopover()
    this.trigger.setAttribute("aria-expanded", "false")
    const calendar = this.calendar
    this.calendar = null
    calendar.destroy()
    this.host.replaceChildren()
    if (restoreFocus && this.trigger.isConnected) this.trigger.focus({preventScroll: true})
  },

  keydown(event) {
    if (this.opened && event.key === "Escape") {
      event.preventDefault()
      event.stopImmediatePropagation()
      this.close(true)
    } else if (this.opened && this.panel.contains(event.target) && event.key === "Enter" && event.target.tagName === "INPUT") {
      event.preventDefault()
      // Let a text edit commit locally, but never submit the surrounding form.
      event.target.dispatchEvent(new Event("change", {bubbles: true}))
      event.stopImmediatePropagation()
    } else if (!this.opened && (event.target === this.input || event.target === this.trigger) &&
      (event.key === "F4" || (event.altKey && event.key === "ArrowDown") || (event.target === this.input && event.key === "Enter"))) {
      if (this.trigger.disabled) return
      event.preventDefault()
      event.stopImmediatePropagation()
      this.open()
    }
  },

  position() {
    if (!this.opened) return
    const anchor = this.trigger.getBoundingClientRect()
    for (let parent = this.trigger.parentElement; parent; parent = parent.parentElement) {
      if (["auto", "scroll", "hidden", "clip"].includes(getComputedStyle(parent).overflowY)) {
        const bounds = parent.getBoundingClientRect()
        if (anchor.bottom <= bounds.top || anchor.top >= bounds.bottom) return this.close(false)
      }
    }
    const margin = 8, gap = 6
    const below = innerHeight - anchor.bottom - margin - gap
    const above = anchor.top - margin - gap
    this.panel.style.width = `${Math.min(304, innerWidth - margin * 2)}px`
    this.panel.style.maxHeight = `${Math.max(0, Math.max(above, below))}px`
    const panel = this.panel.getBoundingClientRect()
    const top = below >= panel.height || below >= above ? anchor.bottom + gap : anchor.top - gap - panel.height
    this.panel.style.left = `${Math.max(margin, Math.min(anchor.right - panel.width, innerWidth - panel.width - margin))}px`
    this.panel.style.top = `${Math.max(margin, Math.min(top, innerHeight - panel.height - margin))}px`
  },

  disconnected() {
    this.offline = true
    this.close(false)
    this.enhance()
  },

  reconnected() {
    this.offline = false
    this.enhance()
  },

  destroyed() {
    this.close(false)
    cancelAnimationFrame(this.positionFrame)
    this.resizeObserver.disconnect()
    this.cleanup.forEach(remove => remove())
  }
}

export default DateTimePicker
