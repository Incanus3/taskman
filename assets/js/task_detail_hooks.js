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

export const taskDetailHooks = {
  "TaskmanWeb.Tasks.Detail.TaskLocationBreadcrumbs": TaskLocationBreadcrumbs,
}
