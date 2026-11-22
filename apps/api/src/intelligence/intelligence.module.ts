import { Module } from '@nestjs/common';
import { VideosModule } from '../videos/videos.module.js';
import { IntelligenceController } from './intelligence.controller.js';
import { IntelligenceService } from './intelligence.service.js';

/** Read APIs for AI artifacts: transcript now; report, chapters, topics in Phase 4. */
@Module({
  imports: [VideosModule],
  controllers: [IntelligenceController],
  providers: [IntelligenceService],
})
export class IntelligenceModule {}
