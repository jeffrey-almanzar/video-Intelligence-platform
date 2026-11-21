import { BadRequestException, NotFoundException } from '@nestjs/common';
import { toProblem } from './problem-details.filter.js';

describe('toProblem', () => {
  it('maps HttpExceptions to their status and message', () => {
    expect(toProblem(new NotFoundException('video not found'), '/v1/videos/1')).toEqual({
      type: 'about:blank',
      title: 'not found',
      status: 404,
      detail: 'video not found',
      instance: '/v1/videos/1',
    });
  });

  it('joins validation message arrays', () => {
    const problem = toProblem(new BadRequestException(['title is required', 'size too large']));
    expect(problem.detail).toBe('title is required; size too large');
  });

  it('never leaks details of unknown errors', () => {
    const problem = toProblem(new Error('password=hunter2 in connection string'));
    expect(problem).toEqual({ type: 'about:blank', title: 'internal server error', status: 500 });
  });
});
