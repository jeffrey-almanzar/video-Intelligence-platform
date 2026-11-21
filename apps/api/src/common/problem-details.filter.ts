import {
  type ArgumentsHost,
  Catch,
  type ExceptionFilter,
  HttpException,
  HttpStatus,
  Logger,
} from '@nestjs/common';
import type { Request, Response } from 'express';

/** RFC 9457 "Problem Details for HTTP APIs". */
export type ProblemDetails = {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  [extension: string]: unknown;
};

/**
 * Maps every error to an `application/problem+json` response. Unknown errors become a generic 500,
 * and their details are logged rather than leaked to clients.
 */
@Catch()
export class ProblemDetailsFilter implements ExceptionFilter {
  private readonly logger = new Logger(ProblemDetailsFilter.name);

  catch(exception: unknown, host: ArgumentsHost): void {
    const http = host.switchToHttp();
    const req = http.getRequest<Request>();
    const res = http.getResponse<Response>();
    const problem = toProblem(exception, req.originalUrl);

    if (problem.status >= 500) {
      this.logger.error({ err: exception, path: req.originalUrl }, 'Unhandled error');
    }
    res.status(problem.status).type('application/problem+json').json(problem);
  }
}

export function toProblem(exception: unknown, instance?: string): ProblemDetails {
  if (exception instanceof HttpException) {
    const status = exception.getStatus();
    const body = exception.getResponse();
    const detail =
      typeof body === 'string'
        ? body
        : typeof body === 'object' && body !== null && 'message' in body
          ? Array.isArray(body.message)
            ? body.message.join('; ')
            : String(body.message)
          : exception.message;
    return {
      type: 'about:blank',
      title: HttpStatus[status]?.replaceAll('_', ' ').toLowerCase() ?? 'error',
      status,
      detail,
      ...(instance ? { instance } : {}),
    };
  }
  return {
    type: 'about:blank',
    title: 'internal server error',
    status: HttpStatus.INTERNAL_SERVER_ERROR,
    ...(instance ? { instance } : {}),
  };
}
