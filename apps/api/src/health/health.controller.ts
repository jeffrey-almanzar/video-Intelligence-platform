import { Controller, Get, HttpStatus, Res } from '@nestjs/common';
import type { Response } from 'express';
import { HealthService } from './health.service.js';
import type { ReadinessReport } from './health.types.js';

@Controller('health')
export class HealthController {
  constructor(private readonly health: HealthService) {}

  /** Liveness: the process is running and can serve HTTP. Never checks dependencies,
   * so a database outage doesn't make the orchestrator restart healthy API containers. */
  @Get('live')
  live(): { status: 'ok' } {
    return { status: 'ok' };
  }

  /** Readiness: whether this instance should receive traffic. 503 when a critical dependency is down. */
  @Get('ready')
  async ready(@Res({ passthrough: true }) res: Response): Promise<ReadinessReport> {
    const report = await this.health.readiness();
    res.status(report.status === 'unavailable' ? HttpStatus.SERVICE_UNAVAILABLE : HttpStatus.OK);
    return report;
  }
}
