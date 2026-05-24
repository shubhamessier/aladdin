import * as http from 'http';

const config = {
    cycleIntervalSeconds: 60, // Default to 60 seconds if not provided
};

const logger = {
    info: (msg: any) => console.log(JSON.stringify(msg)),
};

export class HealthCheckServer {
    private lastCycleTimestamp: number = 0;
    private lastCycleState: string = 'INITIALIZING';
    private consecutiveErrors: number = 0;
    private cycleCount: number = 0;

    start(port: number = 8080): void {
        const server = http.createServer((req, res) => {
            if (req.url === '/health') {
                const age = Date.now() - this.lastCycleTimestamp;
                const maxAge = config.cycleIntervalSeconds * 3 * 1000; // 3 missed cycles = unhealthy

                const healthy = age < maxAge && this.lastCycleState !== 'SHUTDOWN';

                res.writeHead(healthy ? 200 : 503, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({
                    status: healthy ? 'healthy' : 'unhealthy',
                    state: this.lastCycleState,
                    lastCycleAgeMs: age,
                    consecutiveErrors: this.consecutiveErrors,
                    totalCycles: this.cycleCount,
                    uptime: process.uptime(),
                    memoryUsageMB: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
                }));
            } else if (req.url === '/metrics') {
                // Prometheus-compatible metrics
                res.writeHead(200, { 'Content-Type': 'text/plain' });
                res.end(this.getPrometheusMetrics());
            } else {
                res.writeHead(404);
                res.end();
            }
        });

        server.listen(port, () => {
            logger.info({ port, msg: 'Health check server started' });
        });
    }

    recordCycle(state: string, errors: number): void {
        this.lastCycleTimestamp = Date.now();
        this.lastCycleState = state;
        this.consecutiveErrors = errors;
        this.cycleCount++;
    }

    private getPrometheusMetrics(): string {
        return `
# HELP guardian_cycle_count Total number of cycles completed
# TYPE guardian_cycle_count counter
guardian_cycle_count ${this.cycleCount}

# HELP guardian_consecutive_errors Number of consecutive errors in the last cycles
# TYPE guardian_consecutive_errors gauge
guardian_consecutive_errors ${this.consecutiveErrors}

# HELP guardian_last_cycle_timestamp_ms Timestamp of the last recorded cycle
# TYPE guardian_last_cycle_timestamp_ms gauge
guardian_last_cycle_timestamp_ms ${this.lastCycleTimestamp}

# HELP guardian_uptime_seconds Uptime of the process in seconds
# TYPE guardian_uptime_seconds gauge
guardian_uptime_seconds ${process.uptime()}

# HELP guardian_memory_usage_bytes Heap memory used
# TYPE guardian_memory_usage_bytes gauge
guardian_memory_usage_bytes ${process.memoryUsage().heapUsed}
        `.trim() + '\\n';
    }
}
