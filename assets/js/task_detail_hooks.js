const TaskLocationBreadcrumbs = {
  mounted() {
    this.wide = window.matchMedia("(min-width: 80rem)")
    this.scheduleFit = () => {
      cancelAnimationFrame(this.fitFrame)
      this.fitFrame = requestAnimationFrame(() => this.fit())
    }
    this.resizeObserver = new ResizeObserver(this.scheduleFit)
    this.resizeObserver.observe(this.el)
    this.wide.addEventListener("change", this.scheduleFit)
    this.scheduleFit()
  },

  updated() {
    this.scheduleFit()
  },

  destroyed() {
    cancelAnimationFrame(this.fitFrame)
    this.resizeObserver?.disconnect()
    this.wide?.removeEventListener("change", this.scheduleFit)
  },

  fit() {
    const track = this.el.querySelector("#task-location-breadcrumb-track")
    const ellipsis = this.el.querySelector("#task-location-ellipsis")
    if (!track || !ellipsis) return

    const optionalSegments = [
      ...track.querySelectorAll("li[data-optional-segment='true']")
    ]
    const containingSegment = track.querySelector("li[data-containing-location]")
    const containingLink = containingSegment?.querySelector("a[data-containing-location]")

    optionalSegments.forEach(segment => segment.style.removeProperty("display"))
    containingLink?.style.removeProperty("max-width")
    ellipsis.hidden = true

    if (!this.wide.matches) return

    const overflows = () => this.visibleWidth(track) > track.clientWidth + 1

    for (const segment of optionalSegments) {
      if (!overflows()) break

      segment.style.display = "none"
      ellipsis.hidden = false
    }

    if (overflows() && containingSegment && containingLink) {
      const otherWidth = this.visibleWidth(track, containingSegment)

      const separatorWidth =
        containingSegment.getBoundingClientRect().width -
        containingLink.getBoundingClientRect().width

      containingLink.style.maxWidth = `${Math.max(
        track.clientWidth - otherWidth - separatorWidth,
        0
      )}px`
    }
  },

  visibleWidth(track, excludedSegment = null) {
    return [...track.children]
      .filter(segment =>
        segment !== excludedSegment &&
          !segment.hidden &&
          getComputedStyle(segment).display !== "none"
      )
      .reduce((width, segment) => width + segment.getBoundingClientRect().width, 0)
  }
}

const RelatedTaskPicker = {
  mounted() {
    this.panel = this.el.closest("#related-picker")
    this.anchor = document.getElementById(this.panel.dataset.anchor)
    this.schedulePosition = () => {
      cancelAnimationFrame(this.positionFrame)
      this.positionFrame = requestAnimationFrame(() => this.position())
    }
    this.onPointerDown = event => {
      if (this.panel.contains(event.target)) return
      this.outsideDismissal = true
      if (!event.target.closest("[data-related-picker-trigger]")) {
        this.pushEvent("close_related_picker", {})
      }
    }
    this.onKeyDown = event => {
      if (event.key !== "Escape") return
      event.preventDefault()
      event.stopImmediatePropagation()
      this.anchor.focus({preventScroll: true})
      this.pushEvent("close_related_picker", {})
    }
    document.addEventListener("pointerdown", this.onPointerDown, true)
    document.addEventListener("keydown", this.onKeyDown, true)
    document.addEventListener("scroll", this.schedulePosition, true)
    window.addEventListener("resize", this.schedulePosition)
    this.resizeObserver = new ResizeObserver(this.schedulePosition)
    this.resizeObserver.observe(this.panel)
    this.resizeObserver.observe(this.anchor)
    this.mutationObserver = new MutationObserver(records => {
      const positionMissing = !this.panel.style.left || !this.panel.style.top || !this.panel.style.width
      if (positionMissing || records.some(record => record.type !== "attributes")) {
        this.schedulePosition()
      }
    })
    this.mutationObserver.observe(this.anchor.closest("#task-detail-main"), {
      childList: true, subtree: true, characterData: true
    })
    this.mutationObserver.observe(this.panel, {attributes: true, attributeFilter: ["style"]})
    if (!this.panel.matches(":popover-open")) this.panel.showPopover()
    this.position()
    this.panel.querySelector("#related-picker-search").focus({preventScroll: true})
  },

  destroyed() {
    cancelAnimationFrame(this.positionFrame)
    this.resizeObserver.disconnect()
    this.mutationObserver.disconnect()
    document.removeEventListener("pointerdown", this.onPointerDown, true)
    document.removeEventListener("keydown", this.onKeyDown, true)
    document.removeEventListener("scroll", this.schedulePosition, true)
    window.removeEventListener("resize", this.schedulePosition)
    const replaced = this.panel.isConnected && this.panel.dataset.anchor !== this.anchor.id
    if (!this.outsideDismissal && !replaced && this.anchor.isConnected) {
      this.anchor.focus({preventScroll: true})
    }
  },

  position() {
    const margin = 12
    const gap = 8
    const anchor = this.anchor.getBoundingClientRect()
    let visible = {left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight}
    for (let parent = this.anchor.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent)
      const bounds = parent.getBoundingClientRect()
      if (["auto", "scroll", "hidden", "clip"].includes(style.overflowY)) {
        visible.top = Math.max(visible.top, bounds.top)
        visible.bottom = Math.min(visible.bottom, bounds.bottom)
      }
      if (["auto", "scroll", "hidden", "clip"].includes(style.overflowX)) {
        visible.left = Math.max(visible.left, bounds.left)
        visible.right = Math.min(visible.right, bounds.right)
      }
    }
    if (anchor.bottom <= visible.top || anchor.top >= visible.bottom ||
        anchor.right <= visible.left || anchor.left >= visible.right) {
      if (!this.closing) {
        this.closing = true
        this.outsideDismissal = true
        this.pushEvent("close_related_picker", {})
      }
      return
    }
    const below = window.innerHeight - anchor.bottom - margin - gap
    const above = anchor.top - margin - gap
    this.panel.style.width = `${Math.min(384, window.innerWidth - 2 * margin)}px`
    this.panel.style.maxHeight = `${Math.max(0, Math.min(480, Math.max(below, above)))}px`
    const panel = this.panel.getBoundingClientRect()
    const top = below >= panel.height || below >= above
      ? anchor.bottom + gap
      : anchor.top - gap - panel.height
    this.panel.style.left = `${Math.max(margin, Math.min(anchor.right - panel.width, window.innerWidth - panel.width - margin))}px`
    this.panel.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - panel.height - margin))}px`
  }
}

export const taskDetailHooks = {
  RelatedTaskPicker,
  "TaskmanWeb.Tasks.Detail.TaskLocationBreadcrumbs": TaskLocationBreadcrumbs,
}
