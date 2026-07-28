"""KQL used by the dashboard.

These target the standard AVD Insights tables in Log Analytics:
  WVDConnections, WVDErrors, WVDCheckpoints, WVDConnectionNetworkData, Perf

NOTE on FSLogix / Azure Files: profile-load timing is derived from
WVDCheckpoints. The checkpoint *names* emitted depend on your AVD agent /
FSLogix version, so the PROFILE_* queries match on names containing
"Profile"/"FSLogix". If your environment uses different checkpoint names,
adjust the `where Name has ...` clause.

Placeholders substituted by azure_client.run_query:
  {bin}     - time bucket size for the selected range
  {HP}      - host-pool filter for WVD* tables ("" when "all")
  {HPLET}   - host-pool host-set `let` block for Perf ("" when "all")
  {HPWHERE} - host-pool Computer filter for Perf ("" when "all")
"""

# ---------- Host pool list (for the filter dropdown) ----------

HOSTPOOLS = """
WVDConnections
| where isnotempty(_ResourceId)
| extend HostPool = tostring(split(_ResourceId, '/')[-1])
| where isnotempty(HostPool)
| distinct HostPool
| order by HostPool asc
"""

# ---------- Connection health ----------

CONN_TIMESERIES = """
WVDConnections
{HP}
| where State in ("Started", "Connected")
| summarize
    Started   = dcountif(CorrelationId, State == "Started"),
    Connected = dcountif(CorrelationId, State == "Connected")
  by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

CONN_ACTIVE_USERS = """
WVDConnections
{HP}
| where State == "Connected"
| summarize Users = dcount(UserName) by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

CONN_BY_TYPE = """
WVDConnections
{HP}
| where State == "Connected"
| summarize Count = dcount(CorrelationId) by ConnectionType
| order by Count desc
"""

CONN_TOP_USERS = """
WVDConnections
{HP}
| where State == "Connected"
| summarize Connections = dcount(CorrelationId) by UserName
| top 10 by Connections desc
"""

# ---------- Errors & issues ----------

ERR_TIMESERIES = """
WVDErrors
{HP}
| summarize Errors = count() by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

ERR_TOP_CODES = """
WVDErrors
{HP}
| summarize Count = count() by CodeSymbolic, Source
| top 12 by Count desc
"""

ERR_BY_HOST = """
WVDErrors
{HP}
| join kind=inner (
    WVDConnections
    | where isnotempty(SessionHostName)
    | summarize arg_max(TimeGenerated, SessionHostName) by CorrelationId
  ) on CorrelationId
| summarize Errors = count() by SessionHostName
| top 12 by Errors desc
"""

# ---------- Host pool performance ----------

HOST_SESSIONS = """
WVDConnections
{HP}
| where State == "Connected"
| where isnotempty(SessionHostName)
| summarize Sessions = dcount(CorrelationId), Users = dcount(UserName)
  by SessionHostName
| top 20 by Sessions desc
"""

PERF_CPU_TIMESERIES = """
{HPLET}
Perf
| where ObjectName == "Processor" and CounterName == "% Processor Time"
        and InstanceName == "_Total"
{HPWHERE}
| summarize AvgCPU = round(avg(CounterValue), 1) by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

PERF_MEM_TIMESERIES = """
{HPLET}
Perf
| where CounterName == "Available MBytes"
{HPWHERE}
| summarize AvgAvailMB = round(avg(CounterValue), 0)
  by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

PERF_CPU_BY_HOST = """
{HPLET}
Perf
| where ObjectName == "Processor" and CounterName == "% Processor Time"
        and InstanceName == "_Total"
{HPWHERE}
| summarize AvgCPU = round(avg(CounterValue), 1) by Computer
| top 20 by AvgCPU desc
"""

# ---------- User experience ----------

UX_RTT_TIMESERIES = """
WVDConnectionNetworkData
{HP}
| summarize
    AvgRTT = round(avg(EstRoundTripTimeInMs), 1),
    P95RTT = round(percentile(EstRoundTripTimeInMs, 95), 1)
  by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

UX_BANDWIDTH_TIMESERIES = """
WVDConnectionNetworkData
{HP}
| summarize AvgKBps = round(avg(EstAvailableBandwidthKBps), 0)
  by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

UX_RTT_BY_HOST = """
WVDConnectionNetworkData
{HP}
| join kind=inner (
    WVDConnections
    | where isnotempty(SessionHostName)
    | summarize arg_max(TimeGenerated, SessionHostName) by CorrelationId
  ) on CorrelationId
| summarize AvgRTT = round(avg(EstRoundTripTimeInMs), 1)
  by SessionHostName
| top 15 by AvgRTT desc
"""

UX_LOGON_DURATION = """
WVDConnections
{HP}
| where State in ("Started", "Connected")
| summarize
    StartTime   = minif(TimeGenerated, State == "Started"),
    ConnectTime = minif(TimeGenerated, State == "Connected")
  by CorrelationId
| where isnotempty(ConnectTime) and isnotempty(StartTime)
| extend ConnectSec = datetime_diff('second', ConnectTime, StartTime)
| where ConnectSec between (0 .. 600)
| summarize
    AvgConnectSec = round(avg(ConnectSec), 1),
    P95ConnectSec = round(percentile(ConnectSec, 95), 1)
  by bin(StartTime, {bin})
| project TimeGenerated = StartTime, AvgConnectSec, P95ConnectSec
| order by TimeGenerated asc
"""

# ---------- Azure file / FSLogix profile performance ----------

PROFILE_TIMESERIES = """
WVDCheckpoints
{HP}
| where Name has "Profile" or Name has "FSLogix"
| summarize StartT = min(TimeGenerated), EndT = max(TimeGenerated)
  by CorrelationId
| extend ProfileLoadSec = datetime_diff('second', EndT, StartT)
| where ProfileLoadSec between (0 .. 600)
| summarize
    AvgProfileSec = round(avg(ProfileLoadSec), 1),
    P95ProfileSec = round(percentile(ProfileLoadSec, 95), 1)
  by bin(StartT, {bin})
| project TimeGenerated = StartT, AvgProfileSec, P95ProfileSec
| order by TimeGenerated asc
"""

PROFILE_BY_HOST = """
WVDCheckpoints
{HP}
| where Name has "Profile" or Name has "FSLogix"
| summarize StartT = min(TimeGenerated), EndT = max(TimeGenerated)
  by CorrelationId
| extend ProfileLoadSec = datetime_diff('second', EndT, StartT)
| where ProfileLoadSec between (0 .. 600)
| join kind=inner (
    WVDConnections
    | where isnotempty(SessionHostName)
    | summarize arg_max(TimeGenerated, SessionHostName) by CorrelationId
  ) on CorrelationId
| summarize AvgProfileSec = round(avg(ProfileLoadSec), 1)
  by SessionHostName
| top 15 by AvgProfileSec desc
"""

# ---------- DEX factor aggregates (per user / per host) ----------
# Each factor is a separate small query so a missing table only removes
# that one factor from the score instead of breaking the whole DEX view.
# WVDConnectionNetworkData / WVDCheckpoints carry no UserName, so they are
# joined to WVDConnections via CorrelationId.

DEX_USER_CONN = """
WVDConnections
{HP}
| where isnotempty(UserName)
| summarize
    Attempts  = dcountif(CorrelationId, State == "Started"),
    Connected = dcountif(CorrelationId, State == "Connected")
  by UserName
"""

DEX_HOST_CONN = """
WVDConnections
{HP}
| where isnotempty(SessionHostName)
| summarize
    Attempts  = dcountif(CorrelationId, State == "Started"),
    Connected = dcountif(CorrelationId, State == "Connected")
  by SessionHostName
"""

DEX_USER_LOGON = """
WVDConnections
{HP}
| where State in ("Started", "Connected") and isnotempty(UserName)
| summarize
    StartTime   = minif(TimeGenerated, State == "Started"),
    ConnectTime = minif(TimeGenerated, State == "Connected")
  by CorrelationId, UserName
| where isnotempty(StartTime) and isnotempty(ConnectTime)
| extend Sec = datetime_diff('second', ConnectTime, StartTime)
| where Sec between (0 .. 600)
| summarize AvgSec = round(avg(Sec), 1), Count = count() by UserName
"""

DEX_HOST_LOGON = """
WVDConnections
{HP}
| where State in ("Started", "Connected")
| summarize
    StartTime   = minif(TimeGenerated, State == "Started"),
    ConnectTime = minif(TimeGenerated, State == "Connected"),
    Host        = anyif(SessionHostName, isnotempty(SessionHostName))
  by CorrelationId
| where isnotempty(StartTime) and isnotempty(ConnectTime) and isnotempty(Host)
| extend Sec = datetime_diff('second', ConnectTime, StartTime)
| where Sec between (0 .. 600)
| summarize AvgSec = round(avg(Sec), 1), Count = count() by SessionHostName = Host
"""

DEX_USER_RTT = """
WVDConnectionNetworkData
{HP}
| join kind=inner (
    WVDConnections
    | where isnotempty(UserName)
    | summarize arg_max(TimeGenerated, UserName) by CorrelationId
  ) on CorrelationId
| summarize AvgRTT = round(avg(EstRoundTripTimeInMs), 1), Count = count() by UserName
"""

DEX_HOST_RTT = """
WVDConnectionNetworkData
{HP}
| join kind=inner (
    WVDConnections
    | where isnotempty(SessionHostName)
    | summarize arg_max(TimeGenerated, SessionHostName) by CorrelationId
  ) on CorrelationId
| summarize AvgRTT = round(avg(EstRoundTripTimeInMs), 1), Count = count() by SessionHostName
"""

DEX_USER_ERRORS = """
WVDErrors
{HP}
| where isnotempty(UserName)
| summarize Errors = count() by UserName
"""

DEX_HOST_ERRORS = """
WVDErrors
{HP}
| join kind=inner (
    WVDConnections
    | where isnotempty(SessionHostName)
    | summarize arg_max(TimeGenerated, SessionHostName) by CorrelationId
  ) on CorrelationId
| summarize Errors = count() by SessionHostName
"""

DEX_USER_PROFILE = """
WVDCheckpoints
{HP}
| where Name has "Profile" or Name has "FSLogix"
| summarize StartT = min(TimeGenerated), EndT = max(TimeGenerated) by CorrelationId
| extend Sec = datetime_diff('second', EndT, StartT)
| where Sec between (0 .. 600)
| join kind=inner (
    WVDConnections
    | where isnotempty(UserName)
    | summarize arg_max(TimeGenerated, UserName) by CorrelationId
  ) on CorrelationId
| summarize AvgSec = round(avg(Sec), 1), Count = count() by UserName
"""

DEX_HOST_PROFILE = """
WVDCheckpoints
{HP}
| where Name has "Profile" or Name has "FSLogix"
| summarize StartT = min(TimeGenerated), EndT = max(TimeGenerated) by CorrelationId
| extend Sec = datetime_diff('second', EndT, StartT)
| where Sec between (0 .. 600)
| join kind=inner (
    WVDConnections
    | where isnotempty(SessionHostName)
    | summarize arg_max(TimeGenerated, SessionHostName) by CorrelationId
  ) on CorrelationId
| summarize AvgSec = round(avg(Sec), 1), Count = count() by SessionHostName
"""

# Connection stability (RDPSoft DEX factor): frequent short-lived sessions
# indicate disconnect/reconnect churn - a strong "bad experience" signal.

DEX_USER_STABILITY = """
WVDConnections
{HP}
| where State in ("Connected", "Completed") and isnotempty(UserName)
| summarize
    ConnT = minif(TimeGenerated, State == "Connected"),
    EndT  = maxif(TimeGenerated, State == "Completed")
  by CorrelationId, UserName
| where isnotempty(ConnT) and isnotempty(EndT)
| extend DurMin = datetime_diff('minute', EndT, ConnT)
| where DurMin >= 0
| summarize Sessions = count(), ShortSessions = countif(DurMin < 5) by UserName
| extend ShortPct = round(100.0 * ShortSessions / Sessions, 1)
"""

DEX_HOST_STABILITY = """
WVDConnections
{HP}
| where State in ("Connected", "Completed") and isnotempty(SessionHostName)
| summarize
    ConnT = minif(TimeGenerated, State == "Connected"),
    EndT  = maxif(TimeGenerated, State == "Completed")
  by CorrelationId, SessionHostName
| where isnotempty(ConnT) and isnotempty(EndT)
| extend DurMin = datetime_diff('minute', EndT, ConnT)
| where DurMin >= 0
| summarize Sessions = count(), ShortSessions = countif(DurMin < 5) by SessionHostName
| extend ShortPct = round(100.0 * ShortSessions / Sessions, 1)
"""

# ---------- Logon milestone waterfall (eG-style logon breakdown) ----------
# Average seconds from the connection Start to each WVDCheckpoints
# milestone. Checkpoint names vary by AVD agent version, so this is
# name-agnostic: it shows the most common milestones and their timing.

DEX_LOGON_PHASES = """
WVDCheckpoints
{HP}
| join kind=inner (
    WVDConnections
    | where State == "Started"
    | summarize StartT = min(TimeGenerated) by CorrelationId
  ) on CorrelationId
| extend Sec = datetime_diff('millisecond', TimeGenerated, StartT) / 1000.0
| where Sec between (0 .. 600)
| summarize AvgSec = round(avg(Sec), 1), P95Sec = round(percentile(Sec, 95), 1), Count = count() by Name
| top 12 by Count desc
| order by AvgSec asc
"""

# ---------- Graphics quality (frame rate / end-to-end delay) ----------
# Requires the WVDConnectionGraphicsDataPreview table (preview feature).
# column_ifexists keeps this resilient to schema differences.

DEX_GRAPHICS_TIMESERIES = """
WVDConnectionGraphicsDataPreview
{HP}
| extend E2E = todouble(column_ifexists("AvgEndToEndDelayInMs", real(null))),
         Fps = todouble(column_ifexists("AvgFramesPerSecond", real(null)))
| summarize AvgE2EMs = round(avg(E2E), 1), AvgFps = round(avg(Fps), 1)
  by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

# ---------- AVD agent health (latest status per session host) ----------

DEX_AGENT_HEALTH = """
WVDAgentHealthStatus
{HP}
| summarize arg_max(TimeGenerated, Status) by SessionHostName
| summarize Hosts = count() by Status
| order by Hosts desc
"""

# ---------- Entity drill-down (user / host detail windows) ----------
# {NAME} / {SHORT} are validated by azure_client.safe_name before being
# embedded (charset excludes quotes and backslashes).

DETAIL_USER_SESSIONS = """
WVDConnections
{HP}
| where UserName =~ "{NAME}"
| extend ClientOS_ = tostring(column_ifexists("ClientOS", ""))
| summarize
    StartT = minif(TimeGenerated, State == "Started"),
    ConnT  = minif(TimeGenerated, State == "Connected"),
    EndT   = maxif(TimeGenerated, State == "Completed"),
    Host   = anyif(SessionHostName, isnotempty(SessionHostName)),
    CType  = any(ConnectionType),
    ClientOS = any(ClientOS_)
  by CorrelationId
| extend ConnectSec = iif(isnotempty(StartT) and isnotempty(ConnT),
    datetime_diff('second', ConnT, StartT), int(null))
| extend DurationMin = iif(isnotempty(ConnT) and isnotempty(EndT),
    datetime_diff('minute', EndT, ConnT), long(null))
| project TimeGenerated = coalesce(StartT, ConnT), Host, ConnectSec,
    DurationMin, CType, ClientOS
| top 25 by TimeGenerated desc
"""

DETAIL_USER_RTT = """
WVDConnectionNetworkData
{HP}
| join kind=inner (
    WVDConnections | where UserName =~ "{NAME}" | distinct CorrelationId
  ) on CorrelationId
| summarize AvgRTT = round(avg(EstRoundTripTimeInMs), 1) by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

DETAIL_USER_ERRORS = """
WVDErrors
{HP}
| where UserName =~ "{NAME}"
| project TimeGenerated, CodeSymbolic, Source, Message = substring(Message, 0, 120)
| top 15 by TimeGenerated desc
"""

DETAIL_USER_PROFILE = """
WVDCheckpoints
{HP}
| where Name has "Profile" or Name has "FSLogix"
| join kind=inner (
    WVDConnections | where UserName =~ "{NAME}" | distinct CorrelationId
  ) on CorrelationId
| summarize StartT = min(TimeGenerated), EndT = max(TimeGenerated) by CorrelationId
| extend ProfileLoadSec = datetime_diff('second', EndT, StartT)
| where ProfileLoadSec between (0 .. 600)
| project TimeGenerated = StartT, ProfileLoadSec
| top 15 by TimeGenerated desc
"""

DETAIL_HOST_USERS = """
WVDConnections
{HP}
| where SessionHostName =~ "{NAME}"
| where State == "Connected" and isnotempty(UserName)
| summarize Sessions = dcount(CorrelationId), LastSeen = max(TimeGenerated) by UserName
| top 20 by Sessions desc
"""

DETAIL_HOST_CPU = """
Perf
| where ObjectName == "Processor" and CounterName == "% Processor Time"
        and InstanceName == "_Total"
| where tolower(tostring(split(Computer, '.')[0])) == "{SHORT}"
| summarize AvgCPU = round(avg(CounterValue), 1) by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

DETAIL_HOST_MEM = """
Perf
| where CounterName == "Available MBytes"
| where tolower(tostring(split(Computer, '.')[0])) == "{SHORT}"
| summarize AvgAvailMB = round(avg(CounterValue), 0) by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

DETAIL_HOST_RTT = """
WVDConnectionNetworkData
{HP}
| join kind=inner (
    WVDConnections | where SessionHostName =~ "{NAME}" | distinct CorrelationId
  ) on CorrelationId
| summarize AvgRTT = round(avg(EstRoundTripTimeInMs), 1) by bin(TimeGenerated, {bin})
| order by TimeGenerated asc
"""

DETAIL_HOST_ERRORS = """
WVDErrors
{HP}
| join kind=inner (
    WVDConnections | where SessionHostName =~ "{NAME}" | distinct CorrelationId
  ) on CorrelationId
| project TimeGenerated, UserName, CodeSymbolic, Source
| top 15 by TimeGenerated desc
"""

# ---------- Overview KPIs (single-value scalars) ----------

KPI_CONNECTIONS = """
WVDConnections
{HP}
| where State in ("Started", "Connected")
| summarize
    Attempts  = dcountif(CorrelationId, State == "Started"),
    Connected = dcountif(CorrelationId, State == "Connected"),
    ActiveUsers = dcountif(UserName, State == "Connected")
"""

KPI_ERRORS = """
WVDErrors
{HP}
| summarize Errors = count(), AffectedUsers = dcount(UserName)
"""

KPI_RTT = """
WVDConnectionNetworkData
{HP}
| summarize AvgRTT = round(avg(EstRoundTripTimeInMs), 1)
"""

KPI_PROFILE = """
WVDCheckpoints
{HP}
| where Name has "Profile" or Name has "FSLogix"
| summarize StartT = min(TimeGenerated), EndT = max(TimeGenerated)
  by CorrelationId
| extend ProfileLoadSec = datetime_diff('second', EndT, StartT)
| where ProfileLoadSec between (0 .. 600)
| summarize AvgProfileSec = round(avg(ProfileLoadSec), 1)
"""
