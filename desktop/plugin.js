/**
 * Sluice desktop pane — a backend health table, a test query and the block report.
 *
 * Contract: PLAN.md:25-33 and DESIGN.md:237-244. Plain ESM with no build step, so the
 * file is loaded uncompiled by the Desktop renderer: the only importable specifiers are
 * `@hermes/plugin-sdk`, `react` and `react/jsx-runtime`, and UI is written with `jsx()` /
 * `jsxs()` calls (no JSX syntax). No `eval`, no dynamic imports, no app-markup queries, no
 * websockets, no secrets: every action goes through this plugin's own REST namespace.
 *
 * Polling runs every 5 s while the document is visible and pauses otherwise, so a hidden
 * window stops touching the backend.
 */
import { useCallback, useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import {
  PALETTE_AREA,
  PANES_AREA,
  Badge,
  Button,
  Input,
  ScrollArea,
  StatusDot,
  ToggleRow,
  host
} from '@hermes/plugin-sdk'

const POLL_MS = 5000

const HEALTH_TONE = {
  ok: 'good',
  idle: 'muted',
  unconfigured: 'muted',
  circuit: 'bad',
  quota: 'warn',
  error: 'bad'
}

const HEALTH_LABEL = {
  ok: 'ok',
  idle: 'idle',
  unconfigured: 'no key',
  circuit: 'circuit',
  quota: 'quota',
  error: 'error'
}

const NUMERIC_FIELDS = [
  ['per_backend_budget_ms', 'Per-backend budget (ms)'],
  ['total_budget_ms', 'Total budget (ms)'],
  ['search_ttl_general', 'Search TTL, general (s)'],
  ['search_ttl_news', 'Search TTL, news (s)'],
  ['extract_ttl', 'Extract TTL (s)'],
  ['negative_ttl', 'Negative cache TTL (s)'],
  ['domain_memory_ttl', 'Domain memory TTL (s)'],
  ['circuit_failures', 'Failures that open a circuit'],
  ['circuit_open_seconds', 'Circuit open (s)'],
  ['circuit_quota_seconds', 'Quota open (s)'],
  ['cache_max_mb', 'Cache cap (MB)']
]

const NUMERIC_KEYS = new Set(NUMERIC_FIELDS.map(([key]) => key))

function ms(value) {
  return typeof value === 'number' ? `${Math.round(value)} ms` : '—'
}

function usd(value) {
  return typeof value === 'number' ? `$${value.toFixed(4)}` : '—'
}

function errorText(err) {
  if (!err) return ''
  return String((err && err.message) || err)
}

function every(ctx, fn, intervalMs) {
  if (typeof ctx.setInterval === 'function') return ctx.setInterval(fn, intervalMs)
  const handle = setInterval(fn, intervalMs)
  return () => clearInterval(handle)
}

function onVisible(ctx, fn) {
  if (typeof ctx.addEventListener !== 'function' || typeof document === 'undefined') return () => {}
  return ctx.addEventListener(document, 'visibilitychange', fn)
}

function isHidden() {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

function useStatus(ctx) {
  const [state, setState] = useState({ data: null, error: '', loading: true, refreshing: false })

  const load = useCallback(async () => {
    try {
      const data = await ctx.rest('/status')
      if (data && data.ok === false) {
        setState((prev) => ({ ...prev, error: String(data.error || 'status failed'), loading: false, refreshing: false }))
        return
      }
      setState({ data, error: '', loading: false, refreshing: false })
    } catch (err) {
      setState((prev) => ({ ...prev, error: errorText(err), loading: false, refreshing: false }))
    }
  }, [ctx])

  useEffect(() => {
    let active = true
    const tick = () => {
      if (!active || isHidden()) return
      void load()
    }
    tick()
    const stopTimer = every(ctx, tick, POLL_MS)
    const stopListener = onVisible(ctx, tick)
    return () => {
      active = false
      stopTimer()
      stopListener()
    }
  }, [ctx, load])

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, refreshing: true }))
    await load()
  }, [load])

  return { ...state, refresh }
}

function Header({ status, onRefresh }) {
  const backends = (status.data && status.data.backends) || []
  const configured = backends.filter((item) => !item.missing).length
  const healthy = backends.filter((item) => item.health === 'ok').length
  const tone = status.error ? 'bad' : healthy > 0 ? 'good' : configured > 0 ? 'warn' : 'muted'

  return jsxs('div', {
    className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-2 py-1.5',
    children: [
      jsx(StatusDot, { tone }),
      jsx('span', { className: 'font-medium text-(--ui-text-primary)', children: 'Sluice' }),
      jsx('span', {
        className: 'text-(--ui-text-tertiary)',
        children: `${configured}/${backends.length || 12} configured`
      }),
      jsx('span', { className: 'flex-1' }),
      jsx(Button, {
        size: 'xs',
        variant: 'outline',
        onClick: onRefresh,
        children: status.refreshing ? 'Refreshing…' : 'Refresh'
      })
    ]
  })
}

function PlanToggle({ ctx, backend, onDone }) {
  const [busy, setBusy] = useState('')
  const apply = async (plan) => {
    if (plan === backend.plan) return
    setBusy(plan)
    try {
      await ctx.rest('/settings', { method: 'POST', body: { values: { [`plan_${backend.id}`]: plan } } })
      await onDone()
    } finally {
      setBusy('')
    }
  }
  const button = (plan, label) =>
    jsx(Button, {
      size: 'xs',
      variant: backend.plan === plan ? 'default' : 'outline',
      onClick: () => void apply(plan),
      children: busy === plan ? '…' : label
    })

  return jsxs('div', {
    className: 'flex items-center gap-1.5',
    children: [
      jsx('span', { className: 'text-(--ui-text-tertiary)', children: 'plan' }),
      button('free', 'free'),
      button('paid', 'paid')
    ]
  })
}

function BackendRow({ ctx, backend, onChanged }) {
  const [open, setOpen] = useState(false)
  const key = backend.key_present ? 'yes' : backend.keyless_capable ? 'keyless' : 'missing'

  const cells = [
    backend.id,
    backend.tier || backend.declared_tier,
    key,
    HEALTH_LABEL[backend.health] || backend.health,
    ms(backend.latency_p50_ms),
    String(backend.calls || 0),
    usd(backend.spend_usd)
  ]

  return jsxs('tbody', {
    children: [
      jsxs('tr', {
        className: 'cursor-pointer border-b border-(--ui-stroke-secondary)/50 hover:bg-(--ui-surface-hover)',
        onClick: () => setOpen((value) => !value),
        children: cells.map((value, index) =>
          jsx(
            'td',
            {
              className: [
                'py-0.5 pr-1 align-middle',
                index === 0 ? 'font-medium text-(--ui-text-primary)' : 'text-(--ui-text-secondary)',
                index >= 4 ? 'text-right tabular-nums' : ''
              ].join(' '),
              children:
                index === 3
                  ? jsxs('span', {
                      className: 'inline-flex items-center gap-1',
                      children: [
                        jsx(StatusDot, { tone: HEALTH_TONE[backend.health] || 'muted' }),
                        value
                      ]
                    })
                  : value
            },
            `${backend.id}-${index}`
          )
        )
      }),
      open
        ? jsxs('tr', {
            className: 'border-b border-(--ui-stroke-secondary)/50 bg-(--ui-surface-inset)',
            children: jsx('td', {
              colSpan: 7,
              className: 'px-1 py-1.5 align-top',
              children: jsxs('div', {
                className: 'flex flex-col gap-1.5',
                children: [
                  jsxs('div', {
                    className: 'flex flex-wrap items-center gap-1.5',
                    children: [
                      jsx(Badge, { variant: 'outline', children: backend.mode }),
                      jsx(Badge, { variant: backend.plan === 'paid' ? 'warn' : 'muted', children: backend.plan }),
                      backend.key_env
                        ? jsx(Badge, { variant: 'muted', children: backend.key_env })
                        : jsx(Badge, { variant: 'muted', children: 'no key needed' })
                    ]
                  }),
                  backend.missing
                    ? jsx('div', { className: 'text-(--ui-text-tertiary)', children: backend.missing })
                    : null,
                  backend.last_error
                    ? jsx('div', {
                        className: 'text-destructive',
                        children: `last error: ${backend.last_error}`
                      })
                    : null,
                  jsx(PlanToggle, { ctx, backend, onDone: onChanged })
                ]
              })
            })
          })
        : null
    ]
  })
}

function BackendTable({ ctx, status, onChanged }) {
  const backends = (status.data && status.data.backends) || []
  const header = ['backend', 'tier', 'key', 'health', 'p50', 'calls', 'spend']

  return jsx('table', {
    className: 'w-full table-fixed border-collapse text-[10px] leading-4',
    children: [
      jsx('thead', {
        children: jsx('tr', {
          className: 'border-b border-(--ui-stroke-secondary) text-(--ui-text-tertiary)',
          children: header.map((label, index) =>
            jsx(
              'th',
              {
                className: ['py-0.5 pr-1 font-medium', index >= 4 ? 'text-right' : 'text-left'].join(' '),
                children: label
              },
              label
            )
          )
        })
      }),
      backends.length
        ? backends.map((backend) =>
            jsx(BackendRow, { ctx, backend, onChanged }, backend.id)
          )
        : jsx('tbody', {
            children: jsx('tr', {
              children: jsx('td', {
                colSpan: 7,
                className: 'py-3 text-(--ui-text-tertiary)',
                children: status.loading ? 'Loading…' : 'No backend state yet.'
              })
            })
          })
    ]
  })
}

function TestBox({ ctx, onDone }) {
  const [query, setQuery] = useState('sluice smoke test')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const run = async () => {
    const text = query.trim()
    if (!text || busy) return
    setBusy(true)
    try {
      const payload = await ctx.rest('/test', { method: 'POST', body: { query: text, limit: 5 } })
      setResult(payload)
      await onDone()
    } catch (err) {
      setResult({ ok: false, error: errorText(err) })
    } finally {
      setBusy(false)
    }
  }

  const line = result
    ? result.ok
      ? `${result.backend || 'routed'} · ${result.tier || '?'} · ${ms(result.latency_ms)}`
      : `failed: ${result.error || 'no backend answered'}`
    : ''

  return jsxs('div', {
    className: 'flex flex-col gap-1 border-t border-(--ui-stroke-secondary) px-2 py-1.5',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-1.5',
        children: [
          jsx(Input, {
            value: query,
            onChange: (event) => setQuery(event.target.value),
            placeholder: 'test query',
            className: 'h-6 flex-1 text-[11px]',
            onKeyDown: (event) => {
              if (event.key === 'Enter') void run()
            }
          }),
          jsx(Button, {
            size: 'xs',
            variant: 'outline',
            onClick: () => void run(),
            children: busy ? '…' : 'Test'
          })
        ]
      }),
      line
        ? jsx('div', {
            className: result && result.ok ? 'text-(--ui-text-secondary)' : 'text-destructive',
            children: line
          })
        : jsx('div', {
            className: 'text-(--ui-text-tertiary)',
            children: 'One query, one backend, the latency it took.'
          })
    ]
  })
}

function BlockReport({ status }) {
  const domains = ((status.data && status.data.domains) || []).filter(
    (entry) => entry && entry.block_class && entry.block_class !== 'ok'
  )
  if (!domains.length) {
    return jsx('div', {
      className: 'px-2 py-1.5 text-(--ui-text-tertiary)',
      children: 'No challenged domains remembered.'
    })
  }
  return jsxs('div', {
    className: 'flex flex-col gap-1 px-2 py-1.5',
    children: [
      jsx('div', { className: 'font-medium text-(--ui-text-secondary)', children: 'block report' }),
      ...domains.slice(0, 20).map((entry) =>
        jsxs(
          'div',
          {
            className: 'flex items-center gap-1.5',
            children: [
              jsx(Badge, { variant: entry.block_class === 'ok' ? 'muted' : 'warn', children: entry.block_class }),
              jsx('span', { className: 'flex-1 truncate text-(--ui-text-primary)', children: entry.domain }),
              jsx('span', {
                className: 'text-(--ui-text-tertiary)',
                children: entry.stage_that_worked ? `via ${entry.stage_that_worked}` : 'no stage yet'
              })
            ]
          },
          entry.domain
        )
      )
    ]
  })
}

function BrowserPanel({ status }) {
  const browsers = (status.data && status.data.browsers) || {}
  const gaps = (status.data && status.data.browser_gaps) || []
  const present = (label, value) =>
    jsxs('div', {
      className: 'flex items-center gap-1.5',
      children: [
        jsx(StatusDot, { tone: value ? 'good' : 'muted' }),
        jsx('span', { className: 'flex-1 text-(--ui-text-secondary)', children: label }),
        jsx('span', { className: 'text-(--ui-text-tertiary)', children: value ? 'present' : 'absent' })
      ]
    })
  return jsxs('div', {
    className: 'flex flex-col gap-1 px-2 py-1.5',
    children: [
      jsx('div', { className: 'font-medium text-(--ui-text-secondary)', children: 'browser engines' }),
      present('Playwright Chromium', browsers.playwright_chromium),
      present('Packaged Chromium', browsers.packaged_chromium),
      present('Camofox server', browsers.camofox_server),
      present('Browserbase key', browsers.browserbase_key),
      ...gaps.map((gap) =>
        jsxs(
          'div',
          {
            className: 'flex flex-col',
            children: [
              jsx('span', { className: 'text-(--ui-text-tertiary)', children: gap.detail }),
              jsx('span', { className: 'text-(--ui-text-secondary)', children: `next: ${gap.next}` })
            ]
          },
          gap.name
        )
      )
    ]
  })
}

function SluicePane({ ctx }) {
  const status = useStatus(ctx)

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col text-[11px] text-(--ui-text-primary)',
    children: [
      jsx(Header, { status, onRefresh: () => void status.refresh() }),
      status.error
        ? jsx('div', {
            className: 'border-b border-(--ui-stroke-secondary) px-2 py-1 text-destructive',
            children: status.error
          })
        : null,
      jsx(ScrollArea, {
        className: 'min-h-0 flex-1',
        children: jsxs('div', {
          className: 'flex flex-col gap-2 pb-2',
          children: [
            jsx(BackendTable, { ctx, status, onChanged: status.refresh }),
            jsx(BlockReport, { status }),
            jsx(BrowserPanel, { status })
          ]
        })
      }),
      jsx(TestBox, { ctx, onDone: status.refresh })
    ]
  })
}

function Field({ label, value, onChange, hint }) {
  return jsxs('label', {
    className: 'flex flex-col gap-0.5',
    children: [
      jsx('span', { className: 'text-(--ui-text-secondary)', children: label }),
      jsx(Input, { value, onChange: (event) => onChange(event.target.value), className: 'h-6 text-[11px]' }),
      hint ? jsx('span', { className: 'text-(--ui-text-tertiary)', children: hint }) : null
    ]
  })
}

function SettingsPage({ ctx }) {
  const [payload, setPayload] = useState(null)
  const [draft, setDraft] = useState({})
  const [message, setMessage] = useState('')
  const [commands, setCommands] = useState([])
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await ctx.rest('/settings')
      setPayload(data)
      setDraft({ ...(data.values || {}) })
    } catch (err) {
      setMessage(errorText(err))
    }
  }, [ctx])

  useEffect(() => {
    void load()
  }, [load])

  const setValue = (key) => (value) => setDraft((prev) => ({ ...prev, [key]: value }))

  const save = async () => {
    setBusy(true)
    setMessage('')
    try {
      const values = {}
      Object.keys(draft).forEach((key) => {
        if (!payload || draft[key] === (payload.values || {})[key]) return
        if (NUMERIC_KEYS.has(key)) {
          const parsed = Number(draft[key])
          if (Number.isFinite(parsed)) values[key] = Math.trunc(parsed)
        } else {
          values[key] = draft[key]
        }
      })
      if (!Object.keys(values).length) {
        setMessage('nothing changed')
        return
      }
      const result = await ctx.rest('/settings', { method: 'POST', body: { values } })
      setMessage(result.ok ? `saved ${(result.written || []).join(', ')}` : `refused: ${result.error}`)
      if (result.ok) await load()
    } catch (err) {
      setMessage(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const plans = (payload && payload.plans) || {}
  const values = draft

  const showRoutes = async () => {
    try {
      const result = await ctx.rest('/setup', { method: 'POST', body: {} })
      setCommands(((result && result.routes) || []).map((route) => route.command.join(' ')))
    } catch (err) {
      setMessage(errorText(err))
    }
  }

  const applyRoutes = async () => {
    setBusy(true)
    try {
      const result = await ctx.rest('/setup', { method: 'POST', body: { apply: true } })
      setMessage(result.ok ? 'Hermes now routes through Sluice' : `failed: ${result.error || 'see the route output'}`)
      setCommands(((result && result.routes) || []).map((route) => route.command.join(' ')))
    } catch (err) {
      setMessage(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return jsx(ScrollArea, {
    className: 'h-full',
    children: jsxs('div', {
      className: 'flex flex-col gap-3 p-3 text-[11px] text-(--ui-text-primary)',
      children: [
        jsxs('div', {
          className: 'flex flex-col gap-1',
          children: [
            jsx('div', { className: 'font-medium', children: 'Routing' }),
            jsx(Field, {
              label: 'Tier order',
              value: values.tier_order || '',
              onChange: setValue('tier_order'),
              hint: 'local,keyless,free,paid — cheapest first'
            }),
            jsx(ToggleRow, {
              label: 'Keyless tiers',
              description: 'Allow anonymous tiers (ddgs, keyless MCP and public endpoints).',
              checked: Boolean(values.keyless_enabled),
              onChange: (on) => setDraft((prev) => ({ ...prev, keyless_enabled: on }))
            }),
            jsx(Field, {
              label: 'SearXNG URL',
              value: values.searxng_url || '',
              onChange: setValue('searxng_url'),
              hint: 'empty disables the local backend'
            })
          ]
        }),
        jsxs('div', {
          className: 'flex flex-col gap-1',
          children: [
            jsx('div', { className: 'font-medium', children: 'Timeouts and cache' }),
            ...NUMERIC_FIELDS.map(([key, label]) =>
              jsx(Field, { label, value: String(values[key] ?? ''), onChange: setValue(key) }, key)
            )
          ]
        }),
        jsxs('div', {
          className: 'flex flex-col gap-1',
          children: [
            jsx('div', { className: 'font-medium', children: 'Plans' }),
            ...Object.keys(plans).map((vendor) =>
              jsxs(
                'div',
                {
                  className: 'flex items-center gap-1.5',
                  children: [
                    jsx('span', { className: 'flex-1 text-(--ui-text-secondary)', children: vendor }),
                    jsx(Button, {
                      size: 'xs',
                      variant: plans[vendor] === 'free' ? 'default' : 'outline',
                      onClick: () =>
                        void ctx
                          .rest('/settings', { method: 'POST', body: { values: { [`plan_${vendor}`]: 'free' } } })
                          .then(load),
                      children: 'free'
                    }),
                    jsx(Button, {
                      size: 'xs',
                      variant: plans[vendor] === 'paid' ? 'default' : 'outline',
                      onClick: () =>
                        void ctx
                          .rest('/settings', { method: 'POST', body: { values: { [`plan_${vendor}`]: 'paid' } } })
                          .then(load),
                      children: 'paid'
                    })
                  ]
                },
                vendor
              )
            )
          ]
        }),
        jsxs('div', {
          className: 'flex flex-wrap items-center gap-1.5',
          children: [
            jsx(Button, { size: 'xs', variant: 'default', onClick: () => void save(), children: busy ? '…' : 'Save settings' }),
            jsx(Button, { size: 'xs', variant: 'outline', onClick: () => void showRoutes(), children: 'Show config route' }),
            jsx(Button, {
              size: 'xs',
              variant: 'outline',
              onClick: () => void applyRoutes(),
              children: 'Route Hermes through Sluice'
            }),
            jsx(Button, {
              size: 'xs',
              variant: 'ghost',
              onClick: () => {
                if (typeof ctx.runAction === 'function') ctx.runAction('nav.settings')
              },
              children: 'Open Settings'
            })
          ]
        }),
        jsx('div', {
          className: 'text-(--ui-text-tertiary)',
          children:
            'Browser identity is your own: turn on browser.use_real_profile in Settings ▸ Browser, and prefer the local Chromium build. Sluice never enables that switch itself.'
        }),
        commands.length
          ? jsx('pre', {
              className: 'whitespace-pre-wrap break-all text-(--ui-text-tertiary)',
              children: commands.join('\n')
            })
          : null,
        message ? jsx('div', { className: 'text-(--ui-text-secondary)', children: message }) : null
      ]
    })
  })
}

export default {
  id: 'sluice',
  name: 'Sluice',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: PANES_AREA,
      title: 'Sluice',
      data: { placement: 'right', width: '380px' },
      render: () => jsx(SluicePane, { ctx })
    })

    if (typeof ctx.registerSettingsPage === 'function') {
      ctx.registerSettingsPage({
        id: 'settings',
        title: 'Sluice',
        icon: 'settings-gear',
        order: 40,
        render: () => jsx(SettingsPage, { ctx })
      })
    }

    const notify = (message) => {
      try {
        host.notify({ kind: 'info', message })
      } catch (err) {
        /* a failed toast must never break the action */
      }
    }

    ctx.registerMany([
      {
        id: 'probe',
        area: PALETTE_AREA,
        data: {
          id: 'sluice.probe',
          label: 'Sluice: probe backends',
          keywords: ['sluice', 'probe', 'backend', 'health'],
          run: async () => {
            const result = await ctx.rest('/probe', { method: 'POST', body: {} })
            const ok = ((result && result.results) || []).filter((item) => item.ok).length
            notify(`Sluice probe: ${ok}/${(result && result.probed) || 0} answered`)
          }
        }
      },
      {
        id: 'test',
        area: PALETTE_AREA,
        data: {
          id: 'sluice.test',
          label: 'Sluice: test query',
          keywords: ['sluice', 'test', 'query', 'search'],
          run: async () => {
            const result = await ctx.rest('/test', { method: 'POST', body: { query: 'sluice smoke test' } })
            notify(
              result.ok
                ? `Sluice: ${result.backend} (${result.tier}) in ${Math.round(result.latency_ms || 0)} ms`
                : `Sluice test failed: ${result.error || 'no backend answered'}`
            )
          }
        }
      },
      {
        id: 'clear-cache',
        area: PALETTE_AREA,
        data: {
          id: 'sluice.cache.clear',
          label: 'Sluice: clear cache',
          keywords: ['sluice', 'cache', 'clear'],
          run: async () => {
            const result = await ctx.rest('/cache/clear', { method: 'POST', body: {} })
            notify(`Sluice cache cleared: ${result.sluice_rows} row(s)`)
          }
        }
      }
    ])
  }
}
