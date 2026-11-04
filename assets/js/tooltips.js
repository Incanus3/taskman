const selector = "[data-tooltip]"
const delay = 450
const gap = 9
const margin = 8

let trigger = null
let timer = null
let leaveTimer = null
let tooltip = null
let label = null
let pointerDown = false

export function dismissTooltip() {
  clearTimeout(timer)
  clearTimeout(leaveTimer)
  timer = leaveTimer = null
  tooltip?.remove()
  tooltip = trigger = label = null
}

function description(target) {
  return target?.dataset.tooltipMode === "description"
}

function content(target) {
  return target.dataset.tooltip || target.getAttribute("aria-label")
}

function valid(target) {
  return target?.isConnected && target.getClientRects().length > 0 &&
    !target.matches(":disabled, [aria-disabled='true']") &&
    !target.closest("[hidden]") &&
    (!target.closest("[popover]") || target.closest("[popover]").matches(":popover-open"))
}

function show(target) {
  timer = null
  if (!valid(target) || content(target) !== label) return dismissTooltip()

  tooltip = document.createElement("div")
  tooltip.setAttribute("role", "tooltip")
  // Sources have their own persistent accessible description; visual timing is independent.
  tooltip.setAttribute("aria-hidden", "true")
  tooltip.className = "taskman-tooltip"
  tooltip.textContent = label
  if (description(target)) tooltip.setAttribute("popover", "manual")
  const container = target.closest("dialog") || document.body
  container.append(tooltip)
  if (description(target)) tooltip.showPopover()

  const targetBounds = target.getBoundingClientRect()
  const tipBounds = tooltip.getBoundingClientRect()
  const above = targetBounds.top >= tipBounds.height + gap + margin
  const top = above
    ? targetBounds.top - tipBounds.height - gap
    : targetBounds.bottom + gap

  tooltip.style.left = `${Math.max(margin, Math.min(
    targetBounds.left + (targetBounds.width - tipBounds.width) / 2,
    innerWidth - tipBounds.width - margin
  ))}px`
  tooltip.style.top = `${Math.max(margin, Math.min(top, innerHeight - tipBounds.height - margin))}px`
}

function schedule(target, immediate = false) {
  clearTimeout(leaveTimer)
  leaveTimer = null
  if (trigger === target) {
    if (immediate && timer) {
      clearTimeout(timer)
      show(target)
    }
    return
  }
  dismissTooltip()
  if (!valid(target)) return
  label = content(target)
  if (!label) return
  trigger = target
  if (immediate) show(target)
  else timer = setTimeout(() => show(target), delay)
}

export function requestDescriptionTooltip(target) {
  if (description(target)) schedule(target)
  else dismissTooltip()
}

function pointerSource(event) {
  const direct = event.target.closest(selector)
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

function leaveSource() {
  if (description(trigger)) {
    // Allow crossing the small placement gap into the tooltip itself.
    if (!leaveTimer) leaveTimer = setTimeout(dismissTooltip, 120)
  } else dismissTooltip()
}

function pointerOver(event) {
  if (tooltip?.contains(event.target)) {
    clearTimeout(leaveTimer)
    leaveTimer = null
    return
  }
  const source = pointerSource(event)
  if (source) schedule(source)
  else if (trigger) leaveSource()
}

document.addEventListener("pointerover", pointerOver)
document.addEventListener("pointermove", event => {
  if (event.target.closest("[data-tooltip-scope]") || trigger?.hasAttribute("data-tooltip-pass-through")) {
    pointerOver(event)
  }
})
document.addEventListener("pointerout", event => {
  if (!trigger || trigger.contains(event.relatedTarget) || tooltip?.contains(event.relatedTarget)) return
  leaveSource()
})

document.addEventListener("focusin", event => {
  if (!pointerDown) {
    const target = event.target.closest(selector)
    schedule(target, !description(target))
  }
})
document.addEventListener("focusout", event => {
  if (trigger?.contains(event.target)) dismissTooltip()
})
document.addEventListener("pointerdown", () => {
  pointerDown = true
  dismissTooltip()
})
document.addEventListener("pointerup", () => { pointerDown = false })
document.addEventListener("pointercancel", () => { pointerDown = false })
// Capture without consuming Escape: the owning picker/filter/dialog still handles it.
document.addEventListener("keydown", event => {
  if (event.key === "Escape") dismissTooltip()
}, true)
document.addEventListener("change", dismissTooltip, true)
document.addEventListener("scroll", dismissTooltip, true)
document.addEventListener("toggle", event => {
  if (event.newState === "closed" && event.target.contains(trigger)) dismissTooltip()
}, true)
document.addEventListener("phx:update", () => {
  if (description(trigger)) dismissTooltip()
})
window.addEventListener("phx:page-loading-start", dismissTooltip)
window.addEventListener("resize", dismissTooltip)
new MutationObserver(() => {
  if (trigger && (!valid(trigger) || content(trigger) !== label)) dismissTooltip()
}).observe(document.documentElement, {
  subtree: true, childList: true, attributes: true,
  attributeFilter: ["data-tooltip", "hidden", "open", "disabled", "aria-disabled"]
})
