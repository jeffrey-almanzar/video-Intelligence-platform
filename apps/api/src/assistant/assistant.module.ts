import { Module } from '@nestjs/common';
import { VideosModule } from '../videos/videos.module.js';
import { AiQaClient } from './ai-qa.client.js';
import { AssistantController } from './assistant.controller.js';
import { AssistantService } from './assistant.service.js';

@Module({
  imports: [VideosModule],
  controllers: [AssistantController],
  providers: [AssistantService, AiQaClient],
})
export class AssistantModule {}
