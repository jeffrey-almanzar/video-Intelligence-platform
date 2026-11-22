import { Module } from '@nestjs/common';
import { StorageModule } from '../storage/storage.module.js';
import { ProcessingController } from './processing.controller.js';
import { ProcessingService } from './processing.service.js';
import { QueueProducer } from './queue.producer.js';
import { ReconcilerService } from './reconciler.service.js';

@Module({
  imports: [StorageModule],
  controllers: [ProcessingController],
  providers: [ProcessingService, QueueProducer, ReconcilerService],
  exports: [ProcessingService, ReconcilerService],
})
export class ProcessingModule {}
