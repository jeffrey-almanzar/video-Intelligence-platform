import { Module } from '@nestjs/common';
import { ProcessingModule } from '../processing/processing.module.js';
import { StorageModule } from '../storage/storage.module.js';
import { VideosController } from './videos.controller.js';
import { VideosService } from './videos.service.js';

@Module({
  imports: [StorageModule, ProcessingModule],
  controllers: [VideosController],
  providers: [VideosService],
  exports: [VideosService],
})
export class VideosModule {}
