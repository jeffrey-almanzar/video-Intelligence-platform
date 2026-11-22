import { Module } from '@nestjs/common';
import { VideosModule } from '../videos/videos.module.js';
import { EventsController } from './events.controller.js';
import { EventsService } from './events.service.js';

@Module({
  imports: [VideosModule],
  controllers: [EventsController],
  providers: [EventsService],
})
export class EventsModule {}
