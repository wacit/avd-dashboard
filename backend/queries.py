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
| where isnotempty(SessionHostName)
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
| where isnotempty(SessionHostName)
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
  by CorrelationId, SessionHostName
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
  by CorrelationId, SessionHostName
| extend ProfileLoadSec = datetime_diff('second', EndT, StartT)
| where ProfileLoadSec between (0 .. 600)
| summarize AvgProfileSec = round(avg(ProfileLoadSec), 1)
  by SessionHostName
| top 15 by AvgProfileSec desc
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
