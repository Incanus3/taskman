const selector = "[data-tooltip]"
const delay = 450
const gap = 9
const margin = 8

let trigger = null
let timer = null
let tooltip = null
let pointerDown = false

function hide() {
  clearTimeout(timer)
  timer = null
  tooltip?.remove()
  tooltip = null
  trigger = null
}

function show(target) {
  if (!target.isConnected || target.matches(":disabled, [aria-disabled='true']")) return

  const label = target.dataset.tooltip || target.getAttribute("aria-label")
  if (!label) return

  tooltip = document.createElement("div")
  tooltip.setAttribute("role", "tooltip")
  tooltip.setAttribute("aria-hidden", "true")
  tooltip.className = "taskman-tooltip"
  tooltip.textContent = label
  document.body.append(tooltip)

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
  if (trigger === target) {
    if (immediate && timer) {
      clearTimeout(timer)
      timer = null
      show(target)
    }
    return
  }
  hide()
  if (!target || target.matches(":disabled, [aria-disabled='true']")) return

  trigger = target
  if (immediate) show(target)
  else timer = setTimeout(() => show(target), delay)
}

document.addEventListener("pointerover", event => {
  schedule(event.target.closest(selector))
})

document.addEventListener("pointerout", event => {
  if (trigger && !trigger.contains(event.relatedTarget)) hide()
})

document.addEventListener("focusin", event => {
  if (!pointerDown) schedule(event.target.closest(selector), true)
})

document.addEventListener("focusout", event => {
  if (trigger?.contains(event.target)) hide()
})

document.addEventListener("pointerdown", () => {
  pointerDown = true
  hide()
})
document.addEventListener("pointerup", () => { pointerDown = false })
document.addEventListener("pointercancel", () => { pointerDown = false })
document.addEventListener("scroll", hide, true)
window.addEventListener("phx:page-loading-start", hide)
window.addEventListener("resize", hide)
