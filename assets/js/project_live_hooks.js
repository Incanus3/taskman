const projectKey = "taskman:selected-project-id:v1"
const includeChildrenKey = "taskman.task-table.include-children"
const visibleStatusesKey = "taskman.task-table.visible-statuses"

// Browser storage may be disabled; mounted navigation and filters still work.
const storage = {
  get(key) {
    try { return window.localStorage.getItem(key) } catch (_error) { return null }
  },
  set(key, value) {
    try { window.localStorage.setItem(key, value) } catch (_error) { /* Unavailable. */ }
  },
  remove(key) {
    try { window.localStorage.removeItem(key) } catch (_error) { /* Unavailable. */ }
  }
}

const MobileProjectDrawer = {
  mounted() {
    this.wasOpen = this.el.dataset.open === "true"
  },

  updated() {
    const open = this.el.dataset.open === "true"

    if (open && !this.wasOpen && window.matchMedia("(max-width: 1023px)").matches) {
      this.el.querySelector("#project-sidebar-close")?.focus()
    } else if (!open && this.wasOpen) {
      document.getElementById("project-sidebar-toggle")?.focus()
    }

    this.wasOpen = open
  }
}

const ProjectMemory = {
  mounted() {
    this.restoreAttempted = false
    this.sync()
  },

  updated() {
    this.sync()
  },

  sync() {
    const projectId = this.el.dataset.projectId
    if (projectId) {
      this.restoreAttempted = false
      storage.set(projectKey, projectId)
      return
    }

    if (this.el.dataset.rootRoute !== "true" ||
        this.el.dataset.preferencesHydrated !== "true" || this.restoreAttempted) return

    this.restoreAttempted = true
    const savedId = storage.get(projectKey)

    if (savedId === null) return
    if (!/^[1-9][0-9]*$/.test(savedId)) {
      this.clearIfCurrent(savedId)
      return
    }

    this.pushEvent("restore_remembered_project", {project_id: savedId}, reply => {
      if (reply.status === "stale") this.clearIfCurrent(savedId)
    })
  },

  clearIfCurrent(id) {
    if (storage.get(projectKey) === id) storage.remove(projectKey)
  }
}

const TaskTablePreferences = {
  mounted() {
    this.allowedStatuses = this.el.dataset.taskStatuses.split(",")
    this.lastURL = window.location.href
    this.onNavigate = () => this.inspectRoute()
    window.addEventListener("phx:navigate", this.onNavigate)

    this.hydratePreferences()

    this.handleEvent("task_table_preferences_changed", preferences => {
      this.persist(preferences)
      const clean = new URL(window.location.href)
      clean.searchParams.delete("include_children")
      clean.searchParams.delete("statuses")
      window.history.replaceState(window.history.state, "", clean.href)
      this.lastURL = window.location.href
    })
  },

  updated() {
    this.inspectRoute()
    if (this.el.dataset.preferencesHydrated === "false") this.hydratePreferences()
  },

  destroyed() {
    window.removeEventListener("phx:navigate", this.onNavigate)
  },

  hydratePreferences() {
    const explicit = this.explicitSnapshot()
    const includeChildren = explicit.include_children ?? this.readIncludeChildren() ?? false
    const statuses = explicit.statuses ?? this.readStatuses() ??
      this.allowedStatuses.filter(status => status !== "will_not_do")

    this.persist(explicit)

    this.pushEvent("hydrate_task_table_preferences", {
      route_key: this.routeKey(),
      include_children: includeChildren,
      statuses
    })
  },

  routeKey() {
    return window.location.pathname + window.location.search
  },

  normalizeStatuses(statuses) {
    if (!Array.isArray(statuses)) return null
    return this.allowedStatuses.filter(status => statuses.includes(status))
  },

  explicitSnapshot() {
    const query = new URL(window.location.href).searchParams
    const snapshot = {}
    const include = query.getAll("include_children")
    const statuses = query.getAll("statuses")
    if (include.length === 1) snapshot.include_children = include[0] === "true"
    if (statuses.length === 1) {
      snapshot.statuses = this.normalizeStatuses(statuses[0].split(","))
    }
    return snapshot
  },

  inspectRoute() {
    const url = window.location.href
    if (url === this.lastURL) return
    this.lastURL = url
    const snapshot = this.explicitSnapshot()
    if (Object.keys(snapshot).length === 0) return
    this.persist(snapshot)
    this.pushEvent("apply_task_table_route_snapshot", {
      route_key: this.routeKey(),
      ...snapshot
    })
  },

  readIncludeChildren() {
    const value = storage.get(includeChildrenKey)
    if (value === "true") return true
    if (value === "false") return false
    if (value !== null) storage.remove(includeChildrenKey)
    return null
  },

  readStatuses() {
    const value = storage.get(visibleStatusesKey)
    if (value === null) return null

    try {
      const parsed = JSON.parse(value)
      const normalized = this.normalizeStatuses(parsed)
      if (normalized !== null) return normalized
    } catch (_error) {
      // Ignore malformed stored selections.
    }

    storage.remove(visibleStatusesKey)
    return null
  },

  persist(preferences) {
    if (preferences.include_children !== undefined) {
      storage.set(includeChildrenKey, String(preferences.include_children))
    }
    if (preferences.statuses !== undefined) {
      storage.set(visibleStatusesKey, JSON.stringify(preferences.statuses))
    }
  }
}

const ShareTaskView = {
  mounted() {
    this.onClick = async () => {
      const share = new URL(window.location.href)
      if (this.el.dataset.sharePath) share.pathname = this.el.dataset.sharePath
      share.searchParams.set("include_children", this.el.dataset.includeChildren)
      share.searchParams.set("statuses", this.el.dataset.statuses)

      const feedback = document.getElementById(`${this.el.id}-feedback`)
      const toast = document.getElementById(`${this.el.id}-toast`)
      const status = document.getElementById(`${this.el.id}-status`)
      const message = document.getElementById(`${this.el.id}-message`)
      const label = document.getElementById(`${this.el.id}-url-label`)
      const input = document.getElementById(`${this.el.id}-url`)
      window.clearTimeout(this.toastTimer)
      toast.hidden = true

      try {
        await navigator.clipboard.writeText(share.href)
        label.hidden = true
        input.hidden = true
        input.value = ""
        status.textContent = "Link copied"
        feedback.hidden = true
        toast.hidden = false
        this.toastTimer = window.setTimeout(() => {
          toast.hidden = true
          status.textContent = ""
        }, 2500)
      } catch (_error) {
        input.value = share.href
        label.hidden = false
        input.hidden = false
        status.textContent = "Copy failed. Select and copy the link below."
        message.textContent = "Copy failed. Select and copy the link below."
        feedback.hidden = false
        input.focus()
        input.select()
      }
    }
    this.el.addEventListener("click", this.onClick)
  },

  destroyed() {
    window.clearTimeout(this.toastTimer)
    this.el.removeEventListener("click", this.onClick)
  }
}

const CommentThreadScroll = {
  mounted() {
    this.taskId = this.el.dataset.taskId
    this.revision = this.el.dataset.revision
    this.saved = {top: 0, following: true}
    this.activity = this.el.querySelector("#task-activity")
    this.thread = this.el.querySelector("#task-comment-thread")
    // Stream reset can zero scrollTop before beforeUpdate; retain the last stable measurement.
    this.onScroll = () => {
      if (!this.patching && this.thread.scrollHeight === this.lastScrollHeight) this.capture()
    }
    this.thread.addEventListener("scroll", this.onScroll)
    this.wasHidden = this.activity.hidden
    this.tabs = document.getElementById("task-detail-tabs")
    this.onTabClick = event => {
      if (event.target.closest("#task-sessions-tab")) this.capture()
    }
    this.tabs?.addEventListener("click", this.onTabClick, true)
    this.visibilityObserver = new MutationObserver(() => {
      const hidden = this.activity.hidden
      if (this.wasHidden && !hidden) this.restore()
      this.wasHidden = hidden
    })
    this.visibilityObserver.observe(this.activity, {attributes: true, attributeFilter: ["hidden"]})
    requestAnimationFrame(() => this.capture())
  },

  beforeUpdate(toEl) {
    if (toEl.dataset.taskId !== this.taskId) return
    if (toEl.dataset.revision !== this.revision && this.visibleWide()) {
      this.patching = true
      this.restoreAfterUpdate = true
    }
  },

  updated() {
    this.thread = this.el.querySelector("#task-comment-thread")
    if (this.el.dataset.taskId !== this.taskId) {
      this.taskId = this.el.dataset.taskId
      this.saved = {top: 0, following: true}
      this.restoreAfterUpdate = true
    }

    if (this.el.dataset.revision !== this.revision) {
      this.revision = this.el.dataset.revision
      if (this.visibleWide()) this.restoreAfterUpdate = true
    }

    if (this.restoreAfterUpdate && this.visibleWide()) {
      cancelAnimationFrame(this.restoreFrame)
      this.restoreFrame = requestAnimationFrame(() => {
        this.restore()
        requestAnimationFrame(() => { this.patching = false })
      })
    }
    this.restoreAfterUpdate = false
  },

  destroyed() {
    cancelAnimationFrame(this.restoreFrame)
    this.thread.removeEventListener("scroll", this.onScroll)
    this.tabs?.removeEventListener("click", this.onTabClick, true)
    this.visibilityObserver?.disconnect()
  },

  visibleWide() {
    return !this.activity.hidden && window.matchMedia("(min-width: 80rem)").matches
  },

  capture() {
    if (!this.visibleWide()) return
    const {scrollTop, scrollHeight, clientHeight} = this.thread
    this.saved = {
      top: scrollTop,
      following: scrollHeight <= clientHeight || scrollHeight - clientHeight - scrollTop <= 2
    }
    this.lastScrollHeight = scrollHeight
  },

  restore() {
    if (!this.visibleWide()) return
    this.thread.scrollTop = this.saved.following ? this.thread.scrollHeight : this.saved.top
    this.lastScrollHeight = this.thread.scrollHeight
  }
}

const CommentDeparture = {
  mounted() {
    this.wasPending = this.el.dataset.pending === "true"
    this.lastTaskId = this.el.dataset.taskId
    this.onBeforeUnload = event => {
      event.preventDefault()
      event.returnValue = true
    }
    this.onInput = event => {
      if (event.target?.id === "task-comment-text") this.syncWarning()
    }
    this.onClick = event => {
      if (this.el.dataset.taskId && event.target.closest("#task-modal") &&
          !event.target.closest("#task-modal-content")) {
        event.preventDefault()
        event.stopImmediatePropagation()
        this.pushEvent("request_task_departure", {
          destination: this.el.dataset.browsePath,
          draft: this.draft()
        })
        return
      }
      if (!this.hasDraft() && this.el.dataset.pending !== "true") return
      const link = event.target.closest("a[href]")
      if (!link || link.target || link.hasAttribute("download") || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const url = new URL(link.href, location.href)
      if (url.origin !== location.origin) return
      const destination = url.pathname + url.search + url.hash
      if (this.sameTask(destination) && this.el.dataset.pending !== "true") return
      event.preventDefault()
      event.stopImmediatePropagation()
      this.pushEvent("request_task_departure", {destination, draft: this.draft()})
    }
    this.onBeforeNavigate = event => {
      if (!event.detail.pop) return
      if (!this.hasDraft() && this.el.dataset.pending !== "true") return
      const url = new URL(event.detail.href)
      const destination = url.pathname + url.search + url.hash
      if (this.sameTask(destination) && this.el.dataset.pending !== "true") return
      event.preventDefault()
      this.pushEvent("request_task_departure", {destination, draft: this.draft()})
    }
    this.onFocusCommentError = () => {
      window.setTimeout(() => {
        const target = document.querySelector("#task-comment-departure-error") ||
          document.querySelector("#task-comment-error") ||
          document.querySelector("#task-comment-text")
        target?.focus()
      }, 0)
    }
    this.onDepartureCommitted = () => {
      this.suppressWarning = true
      this.syncWarning()
    }
    document.addEventListener("input", this.onInput, true)
    document.addEventListener("click", this.onClick, true)
    window.addEventListener("phx:before-navigate", this.onBeforeNavigate)
    window.addEventListener("phx:focus_task_comment_error", this.onFocusCommentError)
    window.addEventListener("phx:comment_departure_committed", this.onDepartureCommitted)
    this.syncWarning()
  },

  updated() {
    const returnedToTask = this.wasPending && this.el.dataset.pending === "false" &&
      this.el.dataset.retainedDestination !== "true" && document.querySelector("#task-modal-content")
    if (returnedToTask) {
      requestAnimationFrame(() => document.querySelector("#task-modal-content")?.focus())
    }
    this.wasPending = this.el.dataset.pending === "true"
    if (this.el.dataset.taskId !== this.lastTaskId) this.suppressWarning = false
    this.lastTaskId = this.el.dataset.taskId
    this.syncWarning()
  },

  destroyed() {
    document.removeEventListener("input", this.onInput, true)
    document.removeEventListener("click", this.onClick, true)
    window.removeEventListener("phx:before-navigate", this.onBeforeNavigate)
    window.removeEventListener("phx:focus_task_comment_error", this.onFocusCommentError)
    window.removeEventListener("phx:comment_departure_committed", this.onDepartureCommitted)
    window.removeEventListener("beforeunload", this.onBeforeUnload)
  },

  draft() {
    return document.querySelector("#task-comment-text")?.value || ""
  },

  hasDraft() {
    return this.draft().trim() !== "" && !!this.el.dataset.taskId
  },

  sameTask(destination) {
    const path = new URL(destination, location.origin).pathname
    return path.endsWith(`/tasks/${this.el.dataset.taskId}`)
  },

  syncWarning() {
    window.removeEventListener("beforeunload", this.onBeforeUnload)
    if (this.hasDraft() && !this.suppressWarning) window.addEventListener("beforeunload", this.onBeforeUnload)
  }
}

export const projectLiveHooks = {
  "TaskmanWeb.ProjectLive.MobileProjectDrawer": MobileProjectDrawer,
  "TaskmanWeb.ProjectLive.ProjectMemory": ProjectMemory,
  "TaskmanWeb.ProjectLive.TaskTablePreferences": TaskTablePreferences,
  "TaskmanWeb.ProjectLive.ShareTaskView": ShareTaskView,
  "TaskmanWeb.ProjectLive.CommentThreadScroll": CommentThreadScroll,
  "TaskmanWeb.ProjectLive.CommentDeparture": CommentDeparture,
}
