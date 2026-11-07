// HEEx boolean data attributes use presence/absence; Leaf expects string booleans.
// Adapt only the Taskman description's public lifecycle, keeping the shipped hook intact.
export const descriptionLeafHook = leaf => ({
  ...leaf,
  mounted(...args) {
    normalizeReadonly(this.el)
    return leaf.mounted.apply(this, args)
  },
  updated(...args) {
    normalizeReadonly(this.el)
    return leaf.updated.apply(this, args)
  },
})

const normalizeReadonly = element => {
  const wrapper = element.closest("[data-description-readonly]")
  if (wrapper) element.dataset.readonly = wrapper.dataset.descriptionReadonly
}

// Keep Leaf's DOM and auxiliary controls outside the parent form's change protocol.
// Markdown snapshots arrive through Leaf's component events instead.
export default {
  mounted() {
    this.isolate = event => {
      if (event.target.closest('[phx-hook="Leaf"]')) event.stopPropagation()
    }
    // Leaf stops handle mousedown, leaving LiveView's outside click origin stale.
    // Its mouseup already opens the menu; keep the later click inside the editor.
    this.isolateBlockHandleClick = event => {
      const handle = event.target.closest("[data-drag-handle]")
      if (handle && this.el.contains(handle)) event.stopPropagation()
    }
    this.preventImageFile = event => {
      const transfer = event.clipboardData || event.dataTransfer
      const image = Array.from(transfer?.items || []).some(item => item.kind === "file" && item.type.startsWith("image/")) ||
        Array.from(transfer?.files || []).some(file => file.type.startsWith("image/"))
      if (image) {
        event.preventDefault()
        event.stopImmediatePropagation()
      }
    }
    this.el.addEventListener("input", this.isolate)
    this.el.addEventListener("change", this.isolate)
    this.el.addEventListener("click", this.isolateBlockHandleClick)
    this.el.addEventListener("paste", this.preventImageFile, true)
    this.el.addEventListener("drop", this.preventImageFile, true)
    this.labelSurfaces()
  },
  updated() { this.labelSurfaces() },
  // These surfaces already have phx-update="ignore" in the Leaf component.
  labelSurfaces() {
    const label = this.el.querySelector("label")
    this.el.querySelectorAll("[contenteditable], textarea").forEach(surface => {
      surface.setAttribute("aria-labelledby", label.id)
      if (surface.hasAttribute("contenteditable")) {
        surface.setAttribute("role", "textbox")
        surface.setAttribute("aria-multiline", "true")
      }
    })
  },
  destroyed() {
    this.el.removeEventListener("input", this.isolate)
    this.el.removeEventListener("change", this.isolate)
    this.el.removeEventListener("click", this.isolateBlockHandleClick)
    this.el.removeEventListener("paste", this.preventImageFile, true)
    this.el.removeEventListener("drop", this.preventImageFile, true)
  },
}
