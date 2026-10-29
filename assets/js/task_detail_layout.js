export default {
  mounted() {
    this.hierarchyPreference = null
    this.closingPopups = new WeakSet()
    this.media = window.matchMedia("(min-width: 64rem)")
    this.scheduleSync = () => {
      cancelAnimationFrame(this.frame)
      this.frame = requestAnimationFrame(() => this.syncHierarchy())
    }
    this.onMediaChange = () => {
      this.revealSelected = true
      this.syncHierarchy()
    }
    this.media.addEventListener("change", this.onMediaChange)
    this.observer = new MutationObserver(this.scheduleSync)
    this.observer.observe(this.el, {
      childList: true, subtree: true,
      attributes: true, attributeFilter: ["aria-current", "data-has-hierarchy", "hidden"]
    })
    this.onNavigate = this.scheduleSync
    window.addEventListener("phx:navigate", this.onNavigate)

    this.onWorkspaceClick = event => {
      const toggle = event.target.closest("#task-hierarchy-toggle")

      if (toggle && this.el.contains(toggle)) {
        const expanded = this.currentExpanded()
        this.rememberAndApply(!expanded)
        return
      }

      const overlay = event.target.closest("#task-hierarchy-overlay")

      if (overlay && this.el.contains(overlay) && this.narrowOverlayExpanded()) {
        this.rememberAndApply(false)
      }
    }

    this.onHierarchyPopupKeydown = event => {
      const menu = this.el.querySelector("[data-hierarchy-popup]:popover-open")
      if (event.key === "Escape" && menu) {
        event.preventDefault()
        event.stopImmediatePropagation()
        this.closeHierarchyPopup(menu, true)
      }
    }
    document.addEventListener("keydown", this.onHierarchyPopupKeydown, true)
    this.onResize = () => {
      this.revealSelected = true
      this.scheduleSync()
    }
    window.addEventListener("resize", this.onResize)
    document.addEventListener("scroll", this.scheduleSync, true)

    this.onWorkspaceKeydown = event => {
      if (event.key === "Escape" && this.narrowOverlayExpanded()) {
        event.preventDefault()
        event.stopPropagation()
        this.rememberAndApply(false)
      }
    }

    this.el.addEventListener("click", this.onWorkspaceClick)
    this.el.addEventListener("keydown", this.onWorkspaceKeydown)
    this.syncHierarchy()
  },

  updated() {
    this.syncHierarchy()
  },

  destroyed() {
    document.removeEventListener("keydown", this.onHierarchyPopupKeydown, true)
    window.removeEventListener("resize", this.onResize)
    document.removeEventListener("scroll", this.scheduleSync, true)
    this.el.removeEventListener("click", this.onWorkspaceClick)
    this.el.removeEventListener("keydown", this.onWorkspaceKeydown)
    this.media.removeEventListener("change", this.onMediaChange)
    window.removeEventListener("phx:navigate", this.onNavigate)
    this.observer.disconnect()
    this.resizeObserver?.disconnect()
    cancelAnimationFrame(this.frame)
  },

  layout() {
    return this.el.querySelector("#task-detail-layout")
  },

  currentExpanded() {
    return this.layout()?.dataset.hierarchyExpanded === "true"
  },

  narrowOverlayExpanded() {
    return !this.media.matches && this.currentExpanded()
  },

  rememberAndApply(expanded) {
    this.hierarchyPreference = expanded
    this.applyExpanded(expanded)
  },

  syncHierarchy() {
    const layout = this.layout()
    if (!layout) {
      this.activeLayout = null
      this.overlayActive = false
      this.selectedPath = null
      this.resizeObserver?.disconnect()
      return
    }

    if (layout !== this.activeLayout) {
      this.activeLayout = layout
      this.overlayActive = false
      this.revealSelected = true
      this.resizeObserver?.disconnect()
      this.resizeObserver = new ResizeObserver(() => {
        this.revealSelected = true
        this.scheduleSync()
      })
      const scroll = layout.querySelector("#task-hierarchy-scroll")
      if (scroll) this.resizeObserver.observe(scroll)
    }

    const selected = layout.querySelector("[aria-current='true']")
    const path = []
    for (let node = selected?.closest("li"); node; node = node.parentElement.closest("li")) {
      path.push(node.id)
    }
    const selectedPath = path.join("/")
    if (selectedPath !== this.selectedPath) this.revealSelected = true
    this.selectedPath = selectedPath
    const contextualDefault = layout.dataset.hasHierarchy === "true"
    this.applyExpanded(this.hierarchyPreference ?? contextualDefault)
  },

  applyExpanded(expanded) {
    const layout = this.layout()
    if (!layout) return

    const panel = layout.querySelector("#task-hierarchy")
    const toggle = layout.querySelector("#task-hierarchy-toggle")
    expanded = expanded && layout.dataset.recovery !== "true" && !!panel && !!toggle
    const wasExpanded = this.expanded
    const wasOverlay = this.overlayActive
    const overlay = expanded && !this.media.matches
    const content = layout.querySelector("#task-detail-content")
    const focusInPanel = panel?.contains(document.activeElement)
    const focusInContent = content?.contains(document.activeElement)
    layout.dataset.hierarchyExpanded = String(expanded)
    content.inert = overlay
    this.expanded = expanded
    this.overlayActive = overlay

    if (!toggle) {
      this.focusOverlay = false
      return
    }

    toggle.setAttribute("aria-expanded", String(expanded))
    toggle.setAttribute(
      "aria-label",
      expanded ? "Collapse task hierarchy" : "Expand task hierarchy"
    )

    layout
      .querySelector("[data-hierarchy-icon='expand']")
      ?.classList.toggle("hidden", expanded)
    layout
      .querySelector("[data-hierarchy-icon='collapse']")
      ?.classList.toggle("hidden", !expanded)

    if (overlay && !wasOverlay && (!focusInPanel || focusInContent)) {
      this.focusOverlay = true
    }
    if (this.focusOverlay && overlay && toggle.getClientRects().length && !toggle.closest("[hidden], [inert]")) {
      toggle.focus({preventScroll: true})
      this.focusOverlay = false
    } else if (!expanded && wasOverlay && focusInPanel) {
      toggle.focus({preventScroll: true})
    }

    if (expanded && (!wasExpanded || this.revealSelected)) {
      const scroll = layout.querySelector("#task-hierarchy-scroll")
      const selected = layout.querySelector("[aria-current='true']")
      if (scroll && selected) {
        const bounds = scroll.getBoundingClientRect()
        const row = selected.getBoundingClientRect()
        if (row.top < bounds.top) scroll.scrollTop += row.top - bounds.top
        else if (row.bottom > bounds.bottom) scroll.scrollTop += row.bottom - bounds.bottom
        if (row.left < bounds.left) scroll.scrollLeft += row.left - bounds.left
        else if (row.right > bounds.right) scroll.scrollLeft += row.right - bounds.right
      }
    }
    if (!expanded) this.focusOverlay = false
    this.revealSelected = false
    this.syncHierarchyPopups(expanded)
  },

  closeHierarchyPopup(menu, restoreFocus) {
    if (this.closingPopups.has(menu)) return
    this.closingPopups.add(menu)
    menu.hidePopover()
    const trigger = document.getElementById(menu.dataset.popupTrigger)
    if (restoreFocus) trigger?.focus({preventScroll: true})
    this.pushEvent(menu.dataset.popupClose, {})
  },

  syncHierarchyPopups(expanded) {
    this.el.querySelectorAll("[data-hierarchy-popup]").forEach(menu => {
      if (!expanded || menu.closest("[inert]")) {
        this.closeHierarchyPopup(menu, false)
        return
      }
      if (this.closingPopups.has(menu)) return
      const trigger = document.getElementById(menu.dataset.popupTrigger)
      if (!menu.matches(":popover-open")) menu.showPopover()
      const anchor = trigger.getBoundingClientRect()
      const gap = 8
      const below = Math.max(0, innerHeight - anchor.bottom - gap * 2)
      const above = Math.max(0, anchor.top - gap * 2)
      const placeBelow = below >= Math.min(menu.scrollHeight, 304) || below >= above
      menu.style.maxHeight = `${placeBelow ? below : above}px`
      const bounds = menu.getBoundingClientRect()
      menu.style.left = `${Math.max(gap, Math.min(anchor.right - bounds.width, innerWidth - bounds.width - gap))}px`
      menu.style.top = `${placeBelow ? anchor.bottom + gap : anchor.top - bounds.height - gap}px`
    })
  }
}
