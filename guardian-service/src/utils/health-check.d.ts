export declare class HealthCheckServer {
    private lastCycleTimestamp;
    private lastCycleState;
    private consecutiveErrors;
    private cycleCount;
    start(port?: number): void;
    recordCycle(state: string, errors: number): void;
    private getPrometheusMetrics;
}
//# sourceMappingURL=health-check.d.ts.map